import dataclasses
import functools
import json
import logging
import pathlib
import typing
from dataclasses import field
from typing import ClassVar, Dict, List, Tuple

import dataclasses_json

if typing.TYPE_CHECKING:
    from .asyn import AsynPort
    from .db import Database, LinterResults
    from .iocsh import IOCShellInterpreter
    from .macro import MacroContext


logger = logging.getLogger(__name__)

# field metadata to use for excluding fields in JSON
md_excluded = dataclasses_json.config(exclude=dataclasses_json.Exclude.ALWAYS)
md_pathlib = dataclasses_json.config(
    encoder=str,
    decoder=pathlib.Path
)


def dataclass(cls=None, slots=False, **kwargs):
    """
    Dataclass wrapper to set ``__slots__`` adapted from:
        https://github.com/python/cpython/pull/24171
    Hopefully, this will be in CPython at some point.

    No-operation if slots=False.
    """
    # TODO: positional-only

    @functools.wraps(dataclasses.dataclass)
    def wrapper(cls):
        cls = dataclasses.dataclass(cls, **kwargs)
        if not slots:
            return dataclasses_json.dataclass_json(cls)

        # Need to create a new class, since we can't set __slots__
        #  after a class has been created.

        # Make sure __slots__ isn't already set.
        if '__slots__' in cls.__dict__:
            raise TypeError(f'{cls.__name__} already specifies __slots__')

        # Create a new dict for our new class.
        cls_dict = dict(cls.__dict__)
        field_names = tuple(f.name for f in dataclasses.fields(cls))
        cls_dict['__slots__'] = field_names
        for field_name in field_names:
            # Remove our attributes, if present. They'll still be
            #  available in _MARKER.
            cls_dict.pop(field_name, None)

        # Remove __dict__ itself.
        cls_dict.pop('__dict__', None)

        # And finally create the class.
        qualname = getattr(cls, '__qualname__', None)
        cls = type(cls)(cls.__name__, cls.__bases__, cls_dict)
        if qualname is not None:
            cls.__qualname__ = qualname

        return dataclasses_json.dataclass_json(cls)

    if cls is None:
        return wrapper

    return wrapper(cls)


@dataclass(repr=False, slots=True)
class LoadContext:
    name: str
    line: int

    def __repr__(self):
        return f"{self.name}:{self.line}"

    def freeze(self):
        return FrozenLoadContext(self.name, self.line)


@dataclass(repr=False, frozen=True, slots=True)
class FrozenLoadContext:
    name: str
    line: int

    def __repr__(self):
        return f"{self.name}:{self.line}"


@dataclass(slots=True)
class IocshCommand:
    context: Tuple[LoadContext, ...]
    command: str


@dataclass(slots=True)
class IocshResult:
    context: Tuple[LoadContext, ...]
    line: str
    outputs: List[str]
    argv: List[str]
    error: str
    redirects: Dict[str, Dict[str, str]]
    result: object


@dataclass(slots=True)
class IocshScript:
    path: str
    lines: Tuple[IocshResult, ...]


@dataclass(slots=True)
class LinterMessage:
    name: str
    file: str
    line: int
    message: str


@dataclass(slots=True)
class LinterWarning(LinterMessage):
    ...


@dataclass(slots=True)
class LinterError(LinterMessage):
    ...


@dataclass(slots=True)
class ShortLinterResults:
    load_count: int
    errors: List[LinterError]
    warnings: List[LinterWarning]
    macros: Dict[str, str]

    @classmethod
    def from_full_results(cls, results: "LinterResults", macros: Dict[str, str]):
        return cls(
            load_count=len(results.records),
            errors=results.errors,
            warnings=results.warnings,
            macros=macros,
        )


@dataclass(slots=True)
class RecordField:
    dtype: str
    name: str
    value: str
    context: Tuple[LoadContext, ...]

    _jinja_format_: ClassVar[dict] = {
        "console": """field({{name}}, "{{value}}")""",
        "console-verbose": """\
field({{name}}, "{{value}}")  # {{dtype}}{% if context %}; {{context[-1]}}{% endif %}\
""",
    }


def get_link_information(link_str: str) -> Tuple[str, str]:
    """Get link information from a DBF_{IN,OUT,FWD}LINK value."""
    if " " in link_str:
        # strip off PP/MS/etc (TODO might be useful later)
        link_str, additional_info = link_str.split(" ", 1)
    else:
        additional_info = ""

    if link_str.startswith("@"):
        # TODO asyn/device links
        raise ValueError("asyn link")
    if not link_str:
        raise ValueError("empty link")

    if link_str.isnumeric():
        # 0 or 1 usually and not a string
        raise ValueError("integral link")

    try:
        float(link_str)
    except Exception:
        # Good, we don't want a float
        ...
    else:
        raise ValueError("float link")

    return link_str, tuple(additional_info.split(" "))


LINK_TYPES = {"DBF_INLINK", "DBF_OUTLINK", "DBF_FWDLINK"}


@dataclass(slots=True)
class RecordInstance:
    context: Tuple[LoadContext, ...]
    name: str
    record_type: str
    fields: Dict[str, RecordField]
    archived: bool = False
    metadata: Dict[str, str] = field(default_factory=dict)
    aliases: List[str] = field(default_factory=list)
    is_grecord: bool = False

    _jinja_format_: ClassVar[dict] = {
        "console": """\
record("{{record_type}}", "{{name}}") {
{% for ctx in context %}
    # {{ctx}}
{% endfor %}
{% for name, field_inst in fields.items() | sort %}
{% set field_text = render_object(field_inst, "console") %}
    {{ field_text | indent(4)}}
{% endfor %}
}
""",
    }

    def get_fields_of_type(self, *types):
        """Get all fields of the matching type(s)."""
        for fld in self.fields.values():
            if fld.dtype in types:
                yield fld

    def get_links(self):
        """Get all links."""
        for fld in self.get_fields_of_type(*LINK_TYPES):
            try:
                link, info = get_link_information(fld.value)
            except ValueError:
                continue
            yield fld, link, info


@dataclass(slots=True)
class WhatRecord:
    owner: str
    instance: RecordInstance
    asyn_ports: List["AsynPort"]
    # TODO:
    # - IOC host info, port?
    # - gateway rule matches?


def _encode_loaded_files(files: Dict[pathlib.Path, pathlib.Path]) -> str:
    return json.dumps(
        [(str(full_fn), str(orig_fn)) for full_fn, orig_fn in files.items()]
    )


def _decode_loaded_files(files: str) -> Dict[pathlib.Path, pathlib.Path]:
    return {
        pathlib.Path(full_fn): pathlib.Path(orig_fn)
        for full_fn, orig_fn in json.loads(files)
    }


@dataclass(slots=False)
class ShellStateBase:
    prompt: str = "epics>"
    variables: dict = field(default_factory=dict)
    string_encoding: str = "latin-1"
    standin_directories: Dict[str, str] = field(default_factory=dict)
    working_directory: pathlib.Path = field(
        default_factory=lambda: pathlib.Path.cwd(),
        metadata=md_pathlib,
    )
    database_definition: "Database" = None
    database: Dict[str, RecordInstance] = field(default_factory=dict)
    load_context: List[LoadContext] = field(default_factory=list)
    asyn_ports: Dict[str, object] = field(default_factory=dict)
    loaded_files: Dict[pathlib.Path, pathlib.Path] = field(
        default_factory=dict,
        metadata=dataclasses_json.config(
            encoder=_encode_loaded_files,
            decoder=_decode_loaded_files,
        ),
    )
    shell: ClassVar["IOCShellInterpreter"] = field(metadata=md_excluded)
    macro_context: ClassVar["MacroContext"] = field(metadata=md_excluded)
