from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional

import lark
from epicsmacrolib import split_iocsh_line

from . import transformer
from .common import (AnyPath, FullLoadContext, ShellStateHandler,
                     StringWithContext)
from .db import PVAFieldReference, RecordInstance
from .transformer import context_from_token

logger = logging.getLogger(__name__)


def _separate_by_class(items, mapping):
    """Separate ``items`` by type into ``mapping`` of collections."""
    # TODO: should remove this entirely and rewrite the transformer
    for item in items:
        if item == ";":
            continue

        container = mapping[type(item)]
        if isinstance(container, list):
            container.append(item)
        else:
            container[item.name] = item


@dataclass
class ConfigurationSetting:
    name: str
    value: str


@dataclass
class VariableAssignment:
    name: str
    value: str


@dataclass
class Command:
    name: str
    arguments: List[str]


@dataclass
class ProtocolDefinition:
    context: FullLoadContext
    name: str
    handlers: Dict[str, HandlerDefinition] = field(default_factory=dict)
    variables: Dict[str, str] = field(default_factory=dict)
    commands: List[Command] = field(default_factory=list)
    config: Dict[str, str] = field(default_factory=dict)


@dataclass
class HandlerDefinition:
    name: str
    commands: List[Command] = field(default_factory=list)


@dataclass
class StreamProtocol:
    """Representation of a StreamDevice protocol."""
    variables: Dict[str, str] = field(default_factory=dict)
    protocols: Dict[str, ProtocolDefinition] = field(default_factory=dict)
    comments: List[str] = field(default_factory=list)
    config: Dict[str, str] = field(default_factory=dict)
    handlers: Dict[str, HandlerDefinition] = field(default_factory=dict)

    @classmethod
    def from_string(
        cls, contents, filename=None,
    ) -> StreamProtocol:
        """Load a protocol file given its string contents."""
        comments = []
        grammar = lark.Lark.open_from_package(
            "whatrecord",
            "streamdevice.lark",
            search_paths=("grammar", ),
            parser="earley",
            maybe_placeholders=False,
            # lexer_callbacks={"COMMENT": comments.append},
        )

        proto = _ProtocolTransformer(cls, filename).transform(
            grammar.parse(contents)
        )
        proto.comments = comments
        return proto

    @classmethod
    def from_file_obj(cls, fp, filename=None) -> StreamProtocol:
        """Load a protocol file given a file object."""
        return cls.from_string(
            fp.read(),
            filename=getattr(fp, "name", filename),
        )

    @classmethod
    def from_file(cls, fn) -> StreamProtocol:
        """
        Load a StreamDevice protocol file.

        Parameters
        ----------
        filename : pathlib.Path or str
            The filename.

        Returns
        -------
        protocol : StreamProtocol
            The StreamDevice protocol.
        """
        with open(fn, "rt") as fp:
            return cls.from_string(fp.read(), filename=fn)


@lark.visitors.v_args(inline=True)
class _ProtocolTransformer(lark.visitors.Transformer):
    def __init__(self, cls, fn, visit_tokens=False):
        super().__init__(visit_tokens=visit_tokens)
        self.fn = str(fn)
        self.cls = cls

    @lark.visitors.v_args(tree=True)
    def protocol(self, body):
        proto = self.cls()
        variables = []
        config_settings = []
        _separate_by_class(
            body.children,
            {
                VariableAssignment: variables,
                ProtocolDefinition: proto.protocols,
                ConfigurationSetting: config_settings,
                HandlerDefinition: proto.handlers,
            }
        )
        proto.config = {
            config.name: config.value
            for config in config_settings
        }
        proto.variables = {
            var.name: var.value
            for var in variables
        }
        return proto

    def assignment(self, variable, value):
        return VariableAssignment(str(variable), str(value))

    def protocol_body(self, *items):
        return sum((getattr(item, "children", [item]) for item in items), [])

    def standalone_variable_ref(self, ref, *_):
        return Command(
            name=str(ref),
            arguments=[],
        )

    def config_set(self, command):
        return ConfigurationSetting(
            name=command.data,
            value=" ".join(command.children),
        )

    value_part = transformer.pass_through

    def value(self, *items):
        return [
            str(item) for item in items
            if item is not None
        ]

    def _generic_command(command_name: str):
        def _wrapped(self, *values):
            args = [
                str(val)
                for value in values
                for val in value
            ]
            return Command(
                name=command_name,
                arguments=args,
            )
        return _wrapped

    disconnect = _generic_command("disconnect")
    connect = _generic_command("connect")
    out = _generic_command("out")
    locals()["in"] = _generic_command("in")  # "in" is reserved, oops
    wait = _generic_command("wait")
    event = _generic_command("event")
    exec = _generic_command("exec")

    def integer(self, value):
        return "".join(v for v in value)

    def hex_byte(self, *digits):
        return "0x" + "".join(digits)

    def octal_byte(self, *digits):
        return "".join(digits)

    def number(self, *digits):
        return "".join(digits)

    def user_defined_command(self, name, args=None):
        return Command(
            name=name,
            arguments=list(args) if args else [],
        )

    user_defined_command_args = transformer.pass_through
    command = transformer.pass_through

    def protocol_name(self, name):
        return StringWithContext(
            name,
            context=context_from_token(self.fn, name),
        )

    def protocol_def(self, name, children):
        defn = ProtocolDefinition(
            name=name,
            context=name.context,
        )
        variables = []
        config_settings = []
        _separate_by_class(
            children,
            {
                VariableAssignment: variables,
                HandlerDefinition: defn.handlers,
                Command: defn.commands,
                ConfigurationSetting: config_settings,
            }
        )
        defn.config = {
            config.name: config.value
            for config in config_settings
        }
        defn.variables = {
            var.name: var.value
            for var in variables
        }
        return defn

    def handler_body(self, *items):
        commands = []
        for item in items:
            if isinstance(item, tuple):
                commands.append(item[0])  # (Command, EOL)
            else:
                commands.append(item)
        return commands

    def handler_def(self, name, children, *_):
        defn = HandlerDefinition(
            name=name,
        )
        _separate_by_class(
            children,
            {
                Command: defn.commands,
            }
        )
        return defn


_handler = ShellStateHandler.generic_handler_decorator


@dataclass
class StreamDeviceState(ShellStateHandler):
    """
    StreamDevice IOC shell state handler / container.

    Contains hooks for StreamDevice-related commands and state information.

    Attributes
    ----------
    protocols : Dict[str, StreamProtocol]
        Loaded StreamDevice protocols by name.
    """

    metadata_key: ClassVar[str] = "streamdevice"
    protocols: Dict[str, StreamProtocol] = field(
        default_factory=dict
    )

    def find_streamdevice_protocol(self, filename: AnyPath) -> pathlib.Path:
        shell_state = self.primary_handler
        assert shell_state is not None
        return shell_state._fix_path_with_search_list(
            filename,
            shell_state.paths_from_env_var("STREAM_PROTOCOL_PATH", default=".")
        )

    def load_streamdevice_protocol(
        self,
        filename: AnyPath,
    ) -> StreamProtocol:
        """Load a StreamDevice protocol file."""
        filename = self.find_streamdevice_protocol(filename)
        key = str(filename)
        if key not in self.protocols:
            shell_state = self.primary_handler
            fn, contents = shell_state.load_file(filename)
            print("loading!", filename, shell_state.loaded_files)
            self.protocols[key] = StreamProtocol.from_string(
                contents,
                filename=fn
            )
        return self.protocols[key]

    def annotate_record(self, record: RecordInstance) -> Optional[Dict[str, Any]]:
        """Hook to annotate a record after being loaded."""
        dtype = record.fields.get("DTYP", None)
        if not dtype or getattr(dtype, "value", None) != "stream":
            return

        info_field = record.fields.get("INP", record.fields.get("OUT", None))
        if not info_field or isinstance(info_field, PVAFieldReference):
            return {
                "error": "INP/OUT not defined correctly"
            }
        if not isinstance(info_field.value, str):
            return {
                "error": "INP/OUT not defined correctly (JSON)"
            }

        info_field = info_field.value.strip()
        results = {}
        try:
            proto_file, proto_name, *proto_args = split_iocsh_line(info_field).argv
            proto_file = proto_file.lstrip("@ ")
        except Exception:
            results["error"] = (
                f"Invalid StreamDevice input/output field: {info_field!r}"
            )
            proto_file = None
            proto_name = None
            proto_args = []
        else:
            try:
                protocol = self.load_streamdevice_protocol(proto_file)
            except Exception as ex:
                results["error"] = f"{ex.__class__.__name__}: {ex}"
            else:
                if proto_name in protocol.protocols:
                    results["protocol"] = protocol.protocols[proto_name]
                else:
                    results["error"] = (
                        f"Unknown protocol {proto_name!r} in {proto_file}; "
                        f"options are: {list(protocol.protocols)}"
                    )

        return {
            "protocol_file": proto_file,
            "protocol_name": proto_name,
            "protocol_args": proto_args,
            **results,
        }
