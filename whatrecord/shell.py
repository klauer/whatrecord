from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import signal
import sys
import textwrap
import traceback
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import (Any, ClassVar, Dict, Generator, Iterable, List, Optional,
                    Tuple, Union)

import apischema
import lark
from epicsmacrolib import IocshRedirect

from . import common, dbtemplate, graph, settings, util
from .access_security import AccessSecurityState
from .asyn import AsynState
from .autosave import AutosaveState
from .common import (AnyPath, FullLoadContext, IocMetadata, IocshCmdArgs,
                     IocshResult, IocshScript, LoadContext, MutableLoadContext,
                     PVRelations, RecordDefinitionAndInstance, RecordInstance,
                     ShellStateHandler, WhatRecord, time_context)
from .db import Database, DatabaseLoadFailure, RecordType
from .format import FormatContext
from .iocsh import parse_iocsh_line
from .macro import MacroContext
from .motor import MotorState
from .streamdevice import StreamDeviceState

logger = logging.getLogger(__name__)


_handler = ShellStateHandler.generic_handler_decorator


@dataclass
class ShellState(ShellStateHandler):
    """
    IOC shell state container.

    Contains hooks for commands and state information.

    This base state handler should only handle epics base-defined IOC shell
    commands, including: paths, variables, database loading, and IOC
    initialization.

    It is the top-level state container, which sub handlers should rely on for
    things like loading files and other core state information.

    Attributes
    ----------
    prompt : str
        The prompt - PS1 - as in "epics>".
    variables : dict
        Shell variables (not environment variables).
    string_encoding : str
        String encoding for byte strings and files.
    macro_context : MacroContext
        Macro context for commands that are evaluated.
    standin_directories : dict
        Rewrite hard-coded directory prefixes by setting::

            standin_directories = {"/replace_this/": "/with/this"}
    loaded_files : Dict[str, str]
        Files loaded, mapped to a hash of their contents.
    working_directory : pathlib.Path
        Current working directory.
    database_definition : Database
        Loaded database definition (dbd).
    database : Dict[str, RecordInstance]
        The IOC database of records.
    pva_database : Dict[str, RecordInstance]
        The IOC database of PVAccess groups.
    aliases : Dict[str, str]
        Alias name to record name.
    load_context : List[MutableLoadContext]
        Current loading context stack (e.g., ``st.cmd`` then
        ``common_startup.cmd``).  Modified in place as scripts are evaluated.
    """

    prompt: str = "epics>"
    variables: Dict[str, str] = field(default_factory=dict)
    string_encoding: str = "latin-1"
    ioc_initialized: bool = False
    standin_directories: Dict[str, str] = field(default_factory=dict)
    working_directory: pathlib.Path = field(
        default_factory=lambda: pathlib.Path.cwd(),
    )
    aliases: Dict[str, str] = field(default_factory=dict)
    database_definition: Optional[Database] = None
    database: Dict[str, RecordInstance] = field(default_factory=dict)
    pva_database: Dict[str, RecordInstance] = field(default_factory=dict)
    load_context: List[MutableLoadContext] = field(default_factory=list)
    loaded_files: Dict[str, str] = field(default_factory=dict)
    macro_context: MacroContext = field(default_factory=MacroContext)
    ioc_info: IocMetadata = field(default_factory=IocMetadata)
    db_add_paths: List[pathlib.Path] = field(default_factory=list)
    database_grammar_version: int = 3

    # Sub-state handlers:
    access_security: AccessSecurityState = field(default_factory=AccessSecurityState)
    asyn: AsynState = field(default_factory=AsynState)
    autosave: AutosaveState = field(default_factory=AutosaveState)
    motor: MotorState = field(default_factory=MotorState)
    streamdevice: StreamDeviceState = field(default_factory=StreamDeviceState)

    _jinja_format_: ClassVar[Dict[str, str]] = {
        "console": textwrap.dedent(
            """\
            {{ obj | classname }}:
            """.rstrip(),
        ),
        "console-verbose": textwrap.dedent(
            """\
            {{ obj | classname }}:
            """.rstrip(),
        )
    }

    def __post_init__(self):
        super().__post_init__()
        self.macro_context.string_encoding = self.string_encoding

    @property
    def sub_handlers(self) -> List[ShellStateHandler]:
        """Handlers which contain their own state."""
        return [
            self.access_security,
            self.asyn,
            self.autosave,
            self.motor,
            self.streamdevice,
        ]

    def load_file(self, filename: AnyPath) -> Tuple[pathlib.Path, str]:
        """Load a file, record its hash, and return its contents."""
        filename = self._fix_path(filename)
        filename = filename.resolve()
        shasum, contents = util.read_text_file_with_hash(
            filename, encoding=self.string_encoding
        )
        self.loaded_files[str(filename)] = shasum
        self.ioc_info.loaded_files[str(filename)] = shasum
        return filename, contents

    def _handle_input_redirect(
        self,
        redir: IocshRedirect,
        shresult: IocshResult,
        recurse: bool = True,
        raise_on_error: bool = False,
    ):
        try:
            filename, contents = self.load_file(redir.name)
        except Exception as ex:
            shresult.error = f"{type(ex).__name__}: {redir.name}"
            yield shresult
            return

        yield shresult
        yield from self.interpret_shell_script_text(
            contents.splitlines(), recurse=recurse, name=filename
        )

    def interpret_shell_line(self, line, recurse=True, raise_on_error=False):
        """Interpret a single shell script line."""
        shresult = parse_iocsh_line(
            line,
            context=self.get_load_context(),
            prompt=self.prompt,
            macro_context=self.macro_context,
            string_encoding=self.string_encoding,
        )
        input_redirects = [redir for redir in shresult.redirects if redir.mode == "r"]
        if shresult.error:
            yield shresult
        elif input_redirects:
            if recurse:
                yield from self._handle_input_redirect(
                    input_redirects[0],
                    shresult,
                    recurse=recurse,
                    raise_on_error=raise_on_error,
                )
        elif shresult.argv:
            try:
                result = self._handle_command(*shresult.argv)
                if result:
                    # Only set if not-None to speed up serialization
                    shresult.result = result
            except Exception as ex:
                if raise_on_error:
                    raise
                ex_details = traceback.format_exc()
                shresult.error = f"Failed to execute: {ex}:\n{ex_details}"

            yield shresult
            if isinstance(shresult.result, IocshCmdArgs):
                yield from self.interpret_shell_line(
                    shresult.result.command, recurse=recurse
                )
        else:
            # Otherwise, nothing to do
            yield shresult

    def interpret_shell_script(
        self,
        filename: Union[pathlib.Path, str],
        recurse: bool = True,
        raise_on_error: bool = False,
    ) -> Generator[IocshResult, None, None]:
        """Load and interpret a shell script named ``filename``."""
        filename, contents = self.load_file(filename)
        yield from self.interpret_shell_script_text(
            contents.splitlines(),
            name=str(filename),
            recurse=recurse,
            raise_on_error=raise_on_error,
        )

    def interpret_shell_script_text(
        self,
        lines: Iterable[str],
        name: str = "unknown",
        recurse: bool = True,
        raise_on_error: bool = False,
    ) -> Generator[IocshResult, None, None]:
        """Interpret a shell script named ``name`` with ``lines`` of text."""
        load_ctx = MutableLoadContext(str(name), 0)
        try:
            self.load_context.append(load_ctx)
            for lineno, line in enumerate(lines, 1):
                load_ctx.line = lineno
                yield from self.interpret_shell_line(
                    line,
                    recurse=recurse,
                    raise_on_error=raise_on_error,
                )
        finally:
            self.load_context.remove(load_ctx)

        # for rec in list(self.database.values()) + list(self.pva_database.values()):
        #     try:
        #         rec.metadata.update(self.annotate_record(rec))
        #     except Exception:
        #         logger.exception("Failed to annotate record: %s", rec.name)

    def get_load_context(self) -> FullLoadContext:
        """Get a FullLoadContext tuple representing where we are now."""
        if not self.load_context:
            return tuple()
        return tuple(ctx.to_load_context() for ctx in self.load_context)

    def _handle_command(self, command, *args):
        """Handle IOC shell 'command' with provided arguments."""
        handler = self._handlers.get(command, None)
        if handler is not None:
            return handler(*args)
        return self.unhandled(command, args)

    def _fix_path(self, filename: AnyPath) -> pathlib.Path:
        """
        Makes filename an absolute path with respect to the working directory.

        Also replaces standin directories, if provided an absolute path.
        """
        filename = str(filename)
        if os.path.isabs(filename):
            for from_, to in self.standin_directories.items():
                if filename.startswith(from_):
                    _, suffix = filename.split(from_, 1)
                    return pathlib.Path(to + suffix)

        return self.working_directory / filename

    @property
    def db_include_paths(self) -> List[pathlib.Path]:
        """Database include paths (EPICS_DB_INCLUDE_PATH)."""
        env_paths = self.paths_from_env_var("EPICS_DB_INCLUDE_PATH")
        if not env_paths:
            return [self.working_directory] + self.db_add_paths
        return env_paths + self.db_add_paths

    def paths_from_env_var(
        self,
        env_var: str,
        *,
        default: Optional[str] = None
    ) -> List[pathlib.Path]:
        """Paths from an environment variable (or macro)."""
        env_var = self.macro_context.get(env_var, default) or ""
        return [
            (self.working_directory / pathlib.Path(path)).resolve()
            # TODO: this is actually OS-dependent (: on linux, ; on Windows)
            for path in env_var.split(":")
        ]

    def _fix_path_with_search_list(
        self,
        filename: Union[str, pathlib.Path],
        include_paths: List[pathlib.Path],
    ) -> pathlib.Path:
        """Given a list of paths, find ``filename``."""
        filename = str(filename)
        if not include_paths or str(filename)[0] in ("/", "\\"):
            # Include path unset or even something resembling a nested path
            # automatically is used as-is
            return self._fix_path(self.working_directory / filename)

        for path in include_paths:
            option = self._fix_path(path) / filename
            if option.exists() and option.is_file():
                return option

        if "/" in filename or "\\" in filename:
            return self._fix_path(self.working_directory / filename)

        paths = list(str(path) for path in include_paths)
        raise FileNotFoundError(
            f"File {filename!r} not found in search path: {paths} "
        )

    def unhandled(self, command, args):
        ...
        # return f"No handler for handle_{command}"

    @_handler
    def handle_iocshRegisterVariable(self, variable: str, value: str = ""):
        self.variables[variable] = value
        return f"Registered variable: {variable!r}={value!r}"

    def env_set_EPICS_BASE(self, path):
        # TODO: slac-specific
        path = str(pathlib.Path(path).resolve())
        version_prefixes = [
            "/reg/g/pcds/epics/base/",
            "/cds/group/pcds/epics/base/",
        ]
        for prefix in version_prefixes:
            if path.startswith(prefix):
                path = path[len(prefix):]
                if "/" in path:
                    path = path.split("/")[0]
                version = path.lstrip("R")
                if self.ioc_info.base_version == settings.DEFAULT_BASE_VERSION:
                    self.ioc_info.base_version = version
                    return f"Set base version: {version}"
                return (
                    f"Found version ({version}) but version already specified:"
                    f" {self.ioc_info.base_version}"
                )

    @_handler
    def handle_epicsEnvSet(self, variable: str, value: str = ""):
        self.macro_context.define(**{variable: value})
        hook = getattr(self, f"env_set_{variable}", None)
        if hook and callable(hook):
            hook_result = hook(value)
            if hook_result:
                return {
                    "hook": hook_result,
                }

    @_handler
    def handle_epicsEnvShow(self):
        return self.macro_context.get_macros()

    def handle_iocshCmd(self, command: str = "", *_):
        # TODO: odd return type, used below
        return IocshCmdArgs(context=self.get_load_context(), command=command)

    @_handler
    def handle_cd(self, path: str = ""):
        if not path:
            raise RuntimeError("Invalid directory path, ignored")

        path = self._fix_path(path)
        if path.is_absolute():
            new_dir = path
        else:
            new_dir = self.working_directory / path

        if not new_dir.exists():
            raise RuntimeError(f"Path does not exist: {new_dir}")

        self.working_directory = new_dir.resolve()
        os.environ["PWD"] = str(self.working_directory)
        return {
            "result": f"New working directory: {self.working_directory}"
        }

    handle_chdir = handle_cd

    @_handler
    def handle_iocInit(self):
        if self.ioc_initialized:
            return {
                "success": False,
                "error": "Already initialized",
            }

        result = {
            "success": True,
        }

        for handler in self.sub_handlers:
            handler_result = handler.pre_ioc_init()
            result.update(handler_result or {})

        self.ioc_initialized = True

        for handler in self.sub_handlers:
            handler_result = handler.post_ioc_init()
            result.update(handler_result or {})

        return result

    @_handler
    def handle_dbLoadDatabase(self, dbd: str, path: str = "", substitutions: str = ""):
        if self.ioc_initialized:
            raise RuntimeError("Database cannot be loaded after iocInit")
        if self.database_definition:
            # TODO: technically this is allowed; we'll need to update
            # raise RuntimeError("dbd already loaded")
            return {
                "error": "whatrecord: TODO multiple dbLoadDatabase"
            }

        dbd_path = self._fix_path_with_search_list(dbd, self.db_include_paths)
        fn, contents = self.load_file(dbd_path)
        macro_context = MacroContext(use_environment=False)
        macro_context.define_from_string(substitutions or "")

        self.database_grammar_version = self.ioc_info.database_version_spec
        try:
            self.database_definition = Database.from_string(
                contents,
                version=self.database_grammar_version,
                filename=fn,
                macro_context=macro_context,
            )
        except lark.exceptions.ParseError as original_ex:
            # Our guess about the EPICS version may have been wrong.
            # Let's see if the dbd is valid for the other version.
            other_spec = {
                3: 4,
                4: 3
            }[self.database_grammar_version]

            try:
                self.database_definition = Database.from_string(
                    contents,
                    version=other_spec,
                    filename=fn,
                    macro_context=macro_context,
                )
            except Exception:
                raise DatabaseLoadFailure(
                    f"Database definition file failed to load. "
                    f"Attempted to load with grammar version {self.database_grammar_version}, "
                    f"and fallback version {other_spec} unsuccessfully."
                ) from original_ex

            # Our initial guess was wrong... Load further database files with
            # the adjusted version.
            self.database_grammar_version = other_spec

        for addpath in self.database_definition.addpaths:
            for path in addpath.path.split(os.pathsep):  # TODO: OS-dependent
                self.db_add_paths.append((dbd_path.parent / path).resolve())

        self.aliases.update(self.database_definition.aliases)
        return {
            "context": [LoadContext(str(fn), 0)],
            "result": "Loaded database",
            "grammar_version": self.database_grammar_version,
            "record_types": list(sorted(self.database_definition.record_types)),
            "drivers": list(sorted(self.database_definition.drivers)),
            "functions": list(sorted(self.database_definition.functions)),
            "breaktables": list(sorted(self.database_definition.breaktables)),
        }

    @_handler
    def handle_dbLoadTemplate(self, filename: str, macros: str = ""):
        filename = self._fix_path_with_search_list(filename, self.db_include_paths)
        filename, contents = self.load_file(filename)

        # TODO this should be multiple load calls for the purposes of context
        result = {
            "context": [LoadContext(str(filename), 0)],
            "total_records": 0,
            "total_groups": 0,
            "loaded_files": [],
        }

        template = dbtemplate.TemplateSubstitution.from_string(contents, filename=filename)
        for sub in template.substitutions:
            database_contents = sub.expand_file(search_paths=self.db_include_paths)
            # TODO loading file twice (ensure it gets added to the loaded_files list)
            self.load_file(sub.filename)
            db = self._load_database(
                filename=str(sub.filename),
                contents=database_contents,
                macros=macros,
                context=self.get_load_context() + sub.context,
            )
            info = {
                "context": [LoadContext(str(sub.filename), 0)],
                "macros": sub.macros,
                "records": len(db.records),
                "groups": len(db.pva_groups),
                "lint": db.lint,
            }
            result["total_records"] += len(db.records)
            result["total_groups"] += len(db.pva_groups)
            result["loaded_files"].append(info)

        return result

    def _load_database(
        self,
        filename: str,
        contents: str,
        macros: str,
        context: FullLoadContext
    ) -> Database:
        macro_context = MacroContext(use_environment=False)
        macros = macro_context.define_from_string(macros or "")

        try:
            db = Database.from_string(
                contents,
                filename=filename,
                dbd=self.database_definition,
                macro_context=macro_context,
                version=self.database_grammar_version,
            )
        except Exception as ex:
            # TODO move this around
            raise DatabaseLoadFailure(
                f"Failed to load {filename}: {type(ex).__name__} {ex}"
            ) from ex

        for name, rec in db.records.items():
            if name not in self.database:
                self.database[name] = rec
                rec.context = context + rec.context
                rec.owner = self.ioc_info.name
            else:
                entry = self.database[name]
                entry.context = entry.context + rec.context
                entry.fields.update(rec.fields)
                # entry.owner = self.ioc_info.name ?

        for name, rec in db.pva_groups.items():
            if name not in self.pva_database:
                self.pva_database[name] = rec
                rec.context = context + rec.context
                rec.owner = self.ioc_info.name
            else:
                entry = self.database[name]
                entry.context = entry.context + rec.context
                entry.fields.update(rec.fields)
                # entry.owner = self.ioc_info.name ?

        self.aliases.update(db.aliases)
        for addpath in db.addpaths:
            for path in addpath.path.split(os.pathsep):  # TODO: OS-dependent
                self.db_add_paths.append((db.parent / path).resolve())

        return db

    @_handler
    def handle_dbLoadRecords(self, filename: str, macros: str = ""):
        if not self.database_definition:
            raise RuntimeError("dbd not yet loaded")
        if self.ioc_initialized:
            raise RuntimeError("Records cannot be loaded after iocInit")

        filename = self._fix_path_with_search_list(filename, self.db_include_paths)
        filename, contents = self.load_file(filename)
        db = self._load_database(
            filename=filename,
            contents=contents,
            macros=macros or "",
            context=self.get_load_context()
        )

        # TODO: add option to annotate all records without requiring
        # per-record whatrecord queries
        # if self.annotate_all:
        #     for record in db.records.values():
        #         record.metadata.update(self.annotate_record(record))

        return {
            "context": [LoadContext(str(filename), 0)],
            "num_records": len(db.records),
            "num_pva_groups": len(db.pva_groups),
            "lint": db.lint,
        }

    def annotate_record(self, record: RecordInstance) -> Dict[str, Any]:
        """Hook to annotate a record after being loaded."""
        result = {}
        for handler in self.sub_handlers:
            try:
                annotation = handler.annotate_record(record)
            except Exception:
                logger.exception(
                    "Record annotation failed for %s with handler %s",
                    record.name, type(handler).__name__
                )
            else:
                if annotation is not None:
                    # record.metadata[handler.metadata_key] = annotation
                    result[handler.metadata_key] = annotation

        return result

    @_handler
    def handle_dbl(self, rtyp: str = "", fields: str = ""):
        ...

    @_handler
    def handle_NDPvaConfigure(
        self,
        portName: str,
        queueSize: int = 0,
        blockingCallbacks: str = "",
        NDArrayPort: str = "",
        NDArrayAddr: str = "",
        pvName: str = "",
        maxBuffers: int = 0,
        maxMemory: int = 0,
        priority: int = 0,
        stackSize: int = 0,
    ):
        """Implicitly creates a PVA group named ``pvName``."""
        metadata = {
            "portName": portName or "",
            "queueSize": queueSize or "",
            "blockingCallbacks": blockingCallbacks or "",
            "NDArrayPort": NDArrayPort or "",
            "NDArrayAddr": NDArrayAddr or "",
            "pvName": pvName or "",
            "maxBuffers": maxBuffers or "",
            "maxMemory": maxMemory or "",
            "priority": priority or "",
            "stackSize": stackSize or "",
        }
        self.pva_database[pvName] = RecordInstance(
            context=self.get_load_context(),
            name=pvName,
            record_type="PVA",
            fields={},
            is_pva=True,
            metadata={"areaDetector": metadata},
        )
        return metadata


@dataclass
class ScriptContainer:
    """
    Aggregate container for any number of LoadedIoc instances.

    Combines databases, sets of loaded files ease of querying.
    """

    database: Dict[str, RecordInstance] = field(default_factory=dict)
    aliases: Dict[str, str] = field(default_factory=dict)
    pva_database: Dict[str, RecordInstance] = field(default_factory=dict)
    scripts: Dict[str, LoadedIoc] = field(default_factory=dict)
    startup_script_to_ioc: Dict[str, str] = field(default_factory=dict)
    #: absolute filename path to sha
    loaded_files: Dict[str, str] = field(default_factory=dict)
    record_types: Dict[str, RecordType] = field(default_factory=dict)
    pv_relations: PVRelations = field(default_factory=dict)

    def add_loaded_ioc(self, loaded: LoadedIoc):
        ioc_name = loaded.metadata.name
        self.scripts[ioc_name] = loaded
        self.startup_script_to_ioc[str(loaded.metadata.script)] = ioc_name

        # TODO: IOCs will have conflicting definitions of records
        self.aliases.update(loaded.shell_state.aliases)
        if loaded.shell_state.database_definition:
            self.record_types.update(
                loaded.shell_state.database_definition.record_types
            )
        graph.combine_relations(
            self.pv_relations, self.database,
            loaded.pv_relations, loaded.shell_state.database,
            record_types=self.record_types,
            aliases=self.aliases,
        )
        self.database.update(loaded.shell_state.database)
        self.pva_database.update(loaded.shell_state.pva_database)
        self.loaded_files.update(loaded.shell_state.loaded_files)

    def whatrec(
        self,
        rec: str,
        field: Optional[str] = None,
        include_pva: bool = True,
        format_option: str = "console",
        file=sys.stdout,
    ) -> List[WhatRecord]:
        fmt = FormatContext()
        result = []
        for name, loaded in self.scripts.items():
            info: Optional[WhatRecord] = loaded.whatrec(
                rec,
                field,
                include_pva=include_pva,
            )
            if info is not None:
                info.ioc = loaded.metadata
                for match in [info.record, info.pva_group]:
                    if file is not None and match is not None:
                        print(fmt.render_object(match, format_option), file=file)
                result.append(info)
        return result


@dataclass
class LoadedIoc:
    name: str
    path: pathlib.Path
    metadata: IocMetadata
    shell_state: ShellState
    script: IocshScript
    load_failure: bool = False
    pv_relations: PVRelations = field(default_factory=dict)

    _jinja_format_: ClassVar[Dict[str, str]] = {
        "file": textwrap.dedent(
            """\
            {{ render_object(script, "file") }}
            """.rstrip(),
        ),
        "console": textwrap.dedent(
            """\
            {{ obj | classname }}:
            path: {{ path }}
            metadata: {% set metadata = render_object(metadata, "console") %}
            {{ metadata | indent(4) }}
            shell_state: {% set shell_state = render_object(shell_state, "console") %}
            {{ shell_state | indent(4) }}
            script:
            {% set script = render_object(script, "console") %}
                {{ script | indent(4) }}
            load_failure: {{ load_failure }}
            """.rstrip(),
        ),
        "console-verbose": textwrap.dedent(
            """\
            {{ obj | classname }}:
            path: {{ path }}
            metadata: {% set metadata = render_object(metadata, "console-verbose") %}
            {{ metadata | indent(4) }}
            shell_state: {% set shell_state = render_object(shell_state, "console-verbose") %}
            {{ shell_state | indent(4) }}
            script:
            {% set script = render_object(script, "console-verbose") %}
            {{ script | indent(4) }}
            load_failure: {{ load_failure }}
            pv_relations: {{ pv_relations }}
            """.rstrip(),
        )
    }

    @classmethod
    def _json_from_cache(cls, md: IocMetadata) -> Optional[dict]:
        """
        Load the serialized JSON from the cache for the given IOC.

        Parameters
        ----------
        md : IocMetadata
            The metadata that identifies the IOC.

        Returns
        -------
        dict or None
            The cached JSON, if available.
        """
        try:
            with open(md.ioc_cache_filename, "rb") as fp:
                return json.load(fp)
        except FileNotFoundError:
            ...
        except json.decoder.JSONDecodeError:
            # Truncated output file, perhaps
            ...

    @classmethod
    def from_cache(cls, md: IocMetadata) -> Optional[LoadedIoc]:
        """
        If available, load the cached data about the provided IOC.

        Parameters
        ----------
        md : IocMetadata
            The metadata that identifies the IOC.

        Returns
        -------
        LoadedIoc or None
        """
        json_dict = cls._json_from_cache(md)
        if json_dict is not None:
            return apischema.deserialize(cls, json_dict)

    def save_to_cache(self) -> bool:
        """Save this instance to the application cache."""
        if not settings.CACHE_PATH:
            return False

        with open(self.metadata.ioc_cache_filename, "wt") as fp:
            json.dump(apischema.serialize(self), fp=fp)
        return True

    @classmethod
    def from_errored_load(
        cls, md: IocMetadata, load_failure: IocLoadFailure
    ) -> LoadedIoc:
        """
        Create a LoadedIoc from a failed load, as indicated by IocLoadFailure.

        Parameters
        ----------
        md : IocMetadata
            The metadata that was used to load the IOC.
        load_failure : IocLoadFailure
            The load failure information.

        Returns
        -------
        LoadedIoc
        """
        exception_line = f"{load_failure.ex_class}: {load_failure.ex_message}"
        error_lines = [exception_line] + load_failure.traceback.splitlines()
        script = IocshScript(
            path=str(md.script),
            lines=tuple(
                IocshResult(line=line, context=(LoadContext("error", lineno),))
                for lineno, line in enumerate(error_lines, 1)
            ),
        )
        md.metadata["exception_class"] = load_failure.ex_class
        md.metadata["exception_message"] = load_failure.ex_message
        md.metadata["traceback"] = load_failure.traceback
        md.metadata["load_failure"] = True
        return cls(
            name=md.name,
            path=md.script,
            metadata=md,
            shell_state=ShellState(),
            script=script,
            load_failure=True,
        )

    @classmethod
    def from_metadata(cls, md: IocMetadata) -> LoadedIoc:
        """
        Generate a LoadedIoc by interpreting the startup script.

        Parameters
        ----------
        md : IocMetadata
            The metadata from the IOC.

        Returns
        -------
        LoadedIoc
        """
        sh = ShellState(ioc_info=md)
        sh.working_directory = md.startup_directory
        sh.macro_context.define(**md.macros)
        sh.standin_directories = md.standin_directories or {}

        # It's not enough to chdir, as we can rely on the environment variable
        # in shell scripts:
        os.environ["PWD"] = str(md.startup_directory)

        script = IocshScript.from_metadata(md, sh=sh)
        return cls(
            name=md.name,
            path=md.script,
            metadata=md,
            shell_state=sh,
            script=script,
            pv_relations=graph.build_database_relations(sh.database),
        )

    def whatrec(
        self, rec: str, field: Optional[str] = None, include_pva: bool = True
    ) -> Optional[WhatRecord]:
        """
        Get record information, optionally including PVAccess results.

        Parameters
        ----------
        rec : str
            The record name (excluding any field).
        field : Optional[str], optional
            An optional field name, such as ``DESC``.  Currently unused.
        include_pva : bool, optional
            Include PVAccess results in the query.

        Returns
        -------
        Optional[WhatRecord]
            Information about the provided record, if available.
        """
        state = self.shell_state
        v3_inst = state.database.get(state.aliases.get(rec, rec), None)
        pva_inst = state.pva_database.get(rec, None) if include_pva else None
        if not v3_inst and not pva_inst:
            return

        what = WhatRecord(
            name=rec,
            ioc=self.metadata,
            record=None,
            pva_group=None,
        )
        if v3_inst is not None:
            if not state.database_definition:
                defn = None
            else:
                defn = state.database_definition.record_types.get(
                    v3_inst.record_type, None
                )
                what.menus = state.database_definition.menus
                # but what about device types and such?

            v3_inst.metadata.update(state.annotate_record(v3_inst))
            what.record = RecordDefinitionAndInstance(defn, v3_inst)

        if pva_inst is not None:
            what.pva_group = pva_inst

        return what


def load_cached_ioc(
    md: IocMetadata,
    allow_failed_load: bool = False,
) -> Optional[LoadedIoc]:
    """
    Load an IOC from the cache.

    Parameters
    ----------
    md : IocMetadata
        The metadata from the IOC, which determines its cache key.
    allow_failed_load : bool, optional
        A previous load of the provided IOC may have been marked as a failure.
        If set, ``load_cached_ioc`` will return the results of the failed load.
        If unset, ``load_cached_ioc`` will return ``None`` and allow the caller
        to treat this as a cache miss and potentially reload it from disk.

    Returns
    -------
    Optional[LoadedIoc]
        The cached LoadedIoc instance.
    """
    cached_md = md.from_cache()
    if cached_md is None:
        logger.debug("Cached metadata unavailable %s", md.name)
        return None

    if md._cache_key != cached_md._cache_key:
        logger.error("Cache key mismatch?! %s %s", md._cache_key, cached_md._cache_key)
        return None

    if allow_failed_load and (
        cached_md.metadata.get("load_failure") or md.looks_like_sh
    ):
        logger.debug(
            "%s allow_failed_load=True; %s, md.looks_like_sh=%s",
            md.name,
            cached_md.metadata.get("load_failure"),
            md.looks_like_sh,
        )
    elif not cached_md.is_up_to_date():
        logger.debug("%s is not up-to-date", md.name)
        return

    try:
        logger.debug("%s is up-to-date; load from cache", md.name)
        return LoadedIoc._json_from_cache(cached_md)
    except FileNotFoundError:
        logger.error("%s is noted as up-to-date; but cache file missing", md.name)

    return None


@dataclass
class IocLoadFailure:
    ex_class: str
    ex_message: str
    traceback: str


@dataclass
class IocLoadResult:
    identifier: Union[int, str]
    load_time: float
    cache_hit: bool
    result: Union[IocLoadFailure, str]


async def async_load_ioc(
    identifier: Union[int, str],
    md: IocMetadata,
    standin_directories,
    use_gdb: bool = True,
    use_cache: bool = True,
) -> IocLoadResult:
    """
    Helper function for loading an IOC in a subprocess and relying on the cache.

    Parameters
    ----------
    standin_directories :
        Stand-in directories to use when loading the IOC.
    identifier : Union[int, str]
        The IOC identifier.
    md : IocMetadata
        Metadata tied to the IOC.
    use_gdb : bool, optional
        Use GDB to analyze the IOC binary for linting of shell script commands.
    use_cache : bool, optional
        Load from the cache for IOCs which have been cached.

    Returns
    -------
    IocLoadResult
        The result of loading, including an identifier which can be used by the
        caller to load the item from disk (as it's faster than using
        multiprocessing).
    """
    if not settings.CACHE_PATH:
        use_cache = False
    with time_context() as ctx:
        try:
            md.standin_directories.update(standin_directories)
            if use_cache:
                cached_ioc = load_cached_ioc(md)
                if cached_ioc:
                    return IocLoadResult(
                        identifier=identifier,
                        load_time=ctx(),
                        cache_hit=True,
                        result="use_cache"
                    )

            loaded = LoadedIoc.from_metadata(md)
            if use_gdb:
                await md.get_binary_information()

            if use_cache:
                loaded.metadata.save_to_cache()
                loaded.save_to_cache()
                # Avoid pickling massive JSON blob; instruct server to load
                # from cache with token 'use_cache'
                serialized = "use_cache"
            else:
                serialized = apischema.serialize(loaded)
        except Exception as ex:
            return IocLoadResult(
                identifier=identifier,
                load_time=ctx(),
                cache_hit=False,
                result=IocLoadFailure(
                    ex_class=type(ex).__name__,
                    ex_message=str(ex),
                    traceback=traceback.format_exc(),
                ),
            )

        return IocLoadResult(
            identifier=identifier,
            load_time=ctx(),
            cache_hit=False,
            result=serialized,
        )


def _load_ioc(
    identifier: Union[int, str],
    md: IocMetadata,
    standin_directories,
    use_gdb: bool = True,
    use_cache: bool = True,
) -> IocLoadResult:
    """A synchronous wrapper around ``async_load_ioc``."""
    return asyncio.run(
        async_load_ioc(
            identifier=identifier, md=md,
            standin_directories=standin_directories, use_gdb=use_gdb,
            use_cache=use_cache
        )
    )


def _sigint_handler(signum, frame):
    """SIGINT handler for subprocesses."""
    logger.error("Subprocess killed with SIGINT; exiting.")
    sys.exit(1)


def _process_init():
    """Subprocess initializer; enables SIGINT handler."""
    signal.signal(signal.SIGINT, _sigint_handler)


async def load_startup_scripts_with_metadata(
    *md_items,
    standin_directories=None,
    processes: int = 8,
    use_gdb: bool = True,
) -> ScriptContainer:
    """
    Load all given startup scripts into a shared ScriptContainer.

    Parameters
    ----------
    *md_items : list of IocMetadata
        List of IOC metadata.
    standin_directories : dict
        Stand-in/substitute directory mapping.
    processes : int
        The number of processes to use when loading.
    """
    total_files = len(md_items)
    total_child_load_time = 0.0

    with time_context() as total_time, ProcessPoolExecutor(
        max_workers=processes, initializer=_process_init
    ) as executor:
        coros = [
            asyncio.wrap_future(
                executor.submit(
                    _load_ioc, identifier=idx, md=md,
                    standin_directories=standin_directories, use_gdb=use_gdb
                )
            )
            for idx, md in enumerate(md_items)
        ]

        for coro in asyncio.as_completed(coros):
            try:
                load_result = await coro
                md = md_items[load_result.identifier]
            except Exception as ex:
                logger.exception(
                    "Internal error while loading: %s: %s [server %.1f s]",
                    type(ex).__name__,
                    ex,
                    total_time(),
                )
                continue

            use_cache = load_result.result == "use_cache"
            if not use_cache:
                loaded = load_result.result
            else:
                try:
                    loaded = load_cached_ioc(md, allow_failed_load=True)
                    if loaded is None:
                        raise ValueError("Cache entry is empty?")
                except Exception as ex:
                    logger.exception(
                        "Internal error while loading cached IOC from disk: "
                        "%s: %s [server %.1f s]",
                        type(ex).__name__,
                        ex,
                        total_time(),
                    )
                    continue

            total_child_load_time += load_result.load_time

            if isinstance(loaded, IocLoadFailure):
                failure_result: IocLoadFailure = loaded
                logger.error(
                    "Failed to load %s in subprocess: %s "
                    "[%.1f s; server %.1f]: %s\n%s",
                    md.name or md.script,
                    failure_result.ex_class,
                    load_result.load_time,
                    total_time(),
                    failure_result.ex_message,
                    (
                        failure_result.traceback
                        if failure_result.ex_class != "FileNotFoundError"
                        else ""
                    ),
                )
                if md.base_version == settings.DEFAULT_BASE_VERSION:
                    md.base_version = "unknown"
                yield md, LoadedIoc.from_errored_load(md, loaded)
                continue

            with time_context() as ctx:
                loaded_ioc = apischema.deserialize(LoadedIoc, loaded)
                logger.info(
                    "Child loaded %s%s in %.1f s, server deserialized in %.1f s",
                    md.name or md.script,
                    " from cache" if load_result.cache_hit else "",
                    load_result.load_time,
                    ctx(),
                )
                yield md, loaded_ioc

    logger.info(
        "Loaded %d startup scripts in %.1f s (wall time) with %d process(es)",
        total_files,
        total_time(),
        processes,
    )
    logger.info(
        "Child processes reported taking a total of %.1f "
        "sec, the total time on %d process(es)",
        total_child_load_time,
        processes,
    )


# TODO: apischema skip still requires forwardref to exist?
common.ShellState = ShellState
