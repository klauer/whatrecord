import asyncio
import collections
import fnmatch
import functools
import gzip
import json
import logging
import os
import pathlib
import re
import tempfile

try:
    import tracemalloc
except ImportError:
    # tracemalloc unavailable on pypy
    tracemalloc = None
from typing import Any, Dict, List, Optional, Tuple, Union

import apischema
import graphviz
from aiohttp import web

from .. import common, gateway, graph, ioc_finder, settings
from ..common import (AnyPath, LoadContext, RecordInstance, StringWithContext,
                      WhatRecord)
from ..shell import (IocshScript, LoadedIoc, ScriptContainer, ShellState,
                     load_startup_scripts_with_metadata)
from .common import (IocGetDuplicatesResponse, IocGetMatchesResponse,
                     IocGetMatchingRecordsResponse, IocMetadata, PluginResults,
                     PVGetInfo, PVGetMatchesResponse, PVRelationshipResponse,
                     PVShortRelationshipResponse, ServerPluginSpec,
                     TooManyRecordsError)
from .util import TaskHandler

TRUE_VALUES = {"1", "true", "True"}

logger = logging.getLogger(__name__)
_log_handler = None
tracemalloc_snapshot = None


def serialized_response(obj: Any) -> web.Response:
    """Return an apischema-serialized JSON response of a dataclass instance."""
    return web.json_response(apischema.serialize(obj))


class ServerLogHandler(logging.Handler):
    def __init__(self, message_count: int = 1000, level="DEBUG"):
        super().__init__(level=level)
        self.formatter = logging.Formatter(
            "%(asctime)s - PID %(process)d %(filename)18s: %(lineno)-3s "
            "%(funcName)-18s %(levelname)-8s %(message)s"
        )
        self.message_count = message_count
        self.messages = collections.deque(maxlen=message_count)

    def emit(self, record):
        self.messages.append(self.format(record))


def compile_patterns(patterns: Tuple[str, ...], flags=re.IGNORECASE, use_regex=False):
    """Compile regular expression (or glob) patterns with `re.compile`."""
    if use_regex:
        return re.compile("|".join(patterns), flags=flags)

    return re.compile(
        "|".join(fnmatch.translate(pattern) for pattern in patterns),
        flags=flags,
    )


def get_patterns(
    query, key: str = "pattern", regex_key: str = "regex"
) -> Tuple[bool, Tuple[str, ...]]:
    """Get glob/regex patterns from a server query."""
    use_regex = query.get(regex_key, "false").lower() in TRUE_VALUES
    default = (".*" if use_regex else "*",)
    return use_regex, tuple(query.getall(key, default))


class ServerState:
    running: bool
    container: ScriptContainer
    gateway_config: gateway.GatewayConfig
    script_loaders: List[ioc_finder._IocInfoFinder]
    ioc_metadata: List[IocMetadata]
    _update_count: int

    def __init__(
        self,
        startup_scripts: Optional[List[str]] = None,
        script_loaders: Optional[List[str]] = None,
        standin_directories: Optional[Dict[str, str]] = None,
        gateway_config: Optional[str] = None,
        plugins: Optional[List[ServerPluginSpec]] = None,
    ):
        self.running = False
        self.container = ScriptContainer()
        self.gateway_config = None
        self.gateway_config_path = gateway_config
        self.ioc_metadata = []
        self.plugins = plugins or []
        self.plugins_by_name = {
            plugin.name: plugin
            for plugin in plugins or []
        }
        self.script_relations = {}
        self.standin_directories = standin_directories or {}
        self.tasks = TaskHandler()
        self.script_loaders = [
            ioc_finder.IocScriptStaticList(startup_scripts or [])
        ] + [
            ioc_finder.IocScriptExternalLoader(loader)
            for loader in script_loaders or []
        ]
        self._update_count = 0

    async def trigger_manual_update(self) -> None:
        """Manual re-scan of everything."""
        updated = await self._update_script_step()
        logger.debug(
            "Manual update trigger. Updates %s",
            "found" if updated else "not found",
        )
        for plugin in self.plugins:
            await self._update_plugin(plugin)

        self.script_relations = graph.build_script_relations(
            self.container.database,
            self.container.pv_relations,
        )

    def dump_file(self, filename: AnyPath, recorded_hash: str = "") -> dict:
        # TODO recorded hash may differ from what's on disk!
        ioc_name = self.container.startup_script_to_ioc.get(str(filename), None)
        if ioc_name:
            logger.debug("File %s is from IOC %s", filename, ioc_name)
            loaded_ioc = self.container.scripts[ioc_name]
            script_info = loaded_ioc.script
            ioc_md = loaded_ioc.metadata
            return {
                "script": apischema.serialize(IocshScript, script_info),
                "ioc": apischema.serialize(IocMetadata, ioc_md),
            }

        try:
            with open(filename, "rt") as fp:
                lines = fp.read().splitlines()
        except Exception as ex:
            lines = [
                f"whatrecord unable to dump file due to "
                f"{ex.__class__.__name__}: {ex}"
            ]

        return {
            "script": None,
            "ioc": None,
            "lines": lines,
        }

    def dump_iocs(self) -> dict:
        return {
            ioc.metadata.name: {
                "md": apischema.serialize(IocMetadata, ioc.metadata),
                "ioc": apischema.serialize(LoadedIoc, ioc),
            }
            for ioc in self.container.scripts.values()
            if ioc.metadata and ioc.metadata.name
        }

    def dump_plugins(self) -> dict:
        return {
            plugin.name: apischema.serialize(
                Optional[PluginResults],
                plugin.results,
            )
            for plugin in self.plugins
        }

    def dump_files(self) -> dict:
        return {
            fn: self.dump_file(fn, hash)
            for fn, hash in self.container.loaded_files.items()
        }

    async def dump_pv_relations(self) -> dict:
        relation_graphs = {}
        for record in self.container.database:
            if record in self.container.pv_relations:
                logger.info(
                    "Generating graph for record %s which has %d relation(s)",
                    record,
                    len(self.container.pv_relations[record]),
                )
                graph = self.get_graph(  # get_graph_rendered
                    (record,),
                    graph_type="record",
                    # format="svg",
                )
                # rendered = await self.get_graph_rendered(
                #     (record,),
                #     graph_type="record",
                #     format="svg",
                # )
                relation_graphs[record] = {
                    "source": graph.source,
                    # "svg": rendered.decode("utf-8"),
                }

        return relation_graphs

    async def dump(self) -> dict:
        dumped = {
            "plugins": self.dump_plugins(),
            "files": self.dump_files(),
            "iocs": self.dump_iocs(),
            "pv_relations": await self.dump_pv_relations(),
        }
        logger.info(
            "Dumped %d plugins, %d files, %d PV relation graphs, and %d IOCs",
            len(dumped["plugins"]),
            len(dumped["files"]),
            len(dumped["pv_relations"]),
            len(dumped["iocs"]),
        )
        return dumped

    async def dump_to_file(
        self,
        filename: pathlib.Path,
        indent: int = 0,
        encoding: str = "utf-8",
    ) -> None:
        if filename.suffix.lower() not in (".json", ".gz"):
            raise RuntimeError(
                f"Output format not supported yet: {filename.suffix}. "
                f"You probably want .json.gz, by the way, if you're intending "
                f"to use the frontend with these results."
            )

        output = await self.dump()
        dumped = json.dumps(output, sort_keys=True, indent=indent)
        if filename.suffix.lower() in (".json", ):
            with open(filename, "wt") as fp:
                print(dumped, file=fp)
        elif filename.suffix.lower() in (".gz", ):
            with gzip.open(filename, "wb") as fp:
                fp.write(dumped.encode(encoding))

    @property
    def iocs_by_name(self) -> Dict[str, IocMetadata]:
        """Dictionary of IOC name to IocMetadata."""
        return {
            ioc.name: ioc
            for ioc in self.ioc_metadata
        }

    def add_or_update_ioc_metadata(self, md: IocMetadata):
        """
        Add a new IOC to monitor by its metadata.

        Note: an assumption is made here that an IOC name is unique among all
        those loaded.
        """
        try:
            existing = self.iocs_by_name[md.name]
        except KeyError:
            self.ioc_metadata.append(md)
        else:
            existing.update(md)

    @property
    def update_count(self) -> int:
        """The number of times IOCs have been updated."""
        return self._update_count

    async def stop(self):
        """Stop any background updates."""
        self.running = False
        await self.tasks.cancel_all(wait=True)

    async def async_init(self, app):
        self.running = True
        self.tasks.create(self._update_loop())
        logger.info(
            "Server plugins enabled: %s",
            ", ".join(plugin.name for plugin in self.plugins)
        )
        for plugin in self.plugins:
            self.tasks.create(self._update_plugin_loop(plugin))

    async def _update_plugin(self, plugin: ServerPluginSpec):
        """Update the specified plugin."""
        with common.time_context() as ctx:
            try:
                await plugin.update()
            except Exception:
                logger.exception(
                    "Failed to update plugin %r [%.1f s]",
                    plugin.name, ctx()
                )
            else:
                logger.info(
                    "Successfully updated plugin %r [%.1f s]",
                    plugin.name, ctx()
                )

    async def _update_plugin_loop(self, plugin: ServerPluginSpec):
        while self.running and self.update_count == 0 and plugin.after_iocs:
            # Wait until IOCs have been loaded before updating this one for the
            # first time.
            await asyncio.sleep(1)

        logger.info("Server plugin %r updates started.", plugin.name)

        while self.running:
            logger.info("Updating plugin: %s", plugin.name)
            await self._update_plugin(plugin)
            # for record, md in info["record_to_metadata"].items():
            #     ...
            await asyncio.sleep(settings.SERVER_SCAN_PERIOD)

        logger.info("Server plugin %r updates finished.", plugin.name)

    async def _update_script_step(self) -> bool:
        """Update scripts from the script loader."""
        logger.info("Checking for new or updated IOCs...")
        await self.update_script_loaders()

        logger.info("Checking for changed scripts and database files...")
        self._load_gateway_config()

        updated = self.get_updated_iocs()
        if not updated:
            logger.info("No changes found.")
            return False

        logger.info(
            "%d IOC%s changed.", len(updated),
            " has" if len(updated) == 1 else "s have"
        )
        for idx, ioc in enumerate(updated[:10], 1):
            logger.info("* %d: %s", idx, ioc.name)
        if len(updated) > 10:
            logger.info("... and %d more", len(updated) - 10)

        with common.time_context() as ctx:
            await self.update_iocs(updated)
            logger.info(
                "Updated %d IOCs in %.1f seconds", len(updated), ctx()
            )
        self.clear_cache()
        return True

    async def _update_loop(self):
        """Update scripts from the script loader and watch for updates."""
        while self.running:
            changes = await self._update_script_step()
            poll_period = settings.SERVER_SCAN_PERIOD
            if not changes:
                poll_period *= 2
            await asyncio.sleep(poll_period)

        logger.info("Server script updates finished.")

    async def update_script_loaders(self):
        """Update scripts from the script loader and watch for updates."""
        for loader in self.script_loaders:
            await loader.update()
            for _, md in loader.scripts.items():
                self.add_or_update_ioc_metadata(md)

    def get_updated_iocs(self) -> List[IocMetadata]:
        """Check loaded IOCs for any changes."""
        updated = [
            md for md in self.ioc_metadata
            if not md.is_up_to_date()
        ]
        for item in list(updated):
            if not item.script or not item.script.exists() or item.looks_like_sh:
                if self.update_count == 0:
                    # Don't attempt another load unless the file exists
                    updated.remove(item)

        return updated

    def _replace_metadata(
        self,
        old_md: IocMetadata,
        new_md: IocMetadata
    ) -> int:
        """Replace the provided metadata information with a new one."""
        idx = self.ioc_metadata.index(old_md)
        self.ioc_metadata.remove(old_md)
        self.ioc_metadata.insert(idx, new_md)
        return idx

    async def update_iocs(self, iocs: List[IocMetadata]):
        """Reload the provided IOCs."""
        async for md, loaded in load_startup_scripts_with_metadata(
            *iocs, standin_directories=self.standin_directories
        ):
            self.container.add_loaded_ioc(loaded)
            self._replace_metadata(md, loaded.metadata)

            # Let plugins update, if possible
            await asyncio.sleep(0)

        with common.time_context() as ctx:
            self.script_relations = graph.build_script_relations(
                self.container.database,
                self.container.pv_relations,
            )

        logger.info("Updated script relations in %.1f s", ctx())
        self._update_count += 1

    def get_plugin_info(self, allow_list: Optional[List[str]] = None) -> Dict[str, Any]:
        """Get plugin information as a dictionary."""
        if allow_list is None:
            allow_list = [plugin.name for plugin in self.plugins]

        return {
            plugin.name: plugin.results_json
            for plugin in self.plugins
            if plugin.name in allow_list and plugin.results
        }

    def get_plugin_nested_keys(self, plugin_name: str) -> List[str]:
        """Get plugin custom nested metadata keys."""
        plugin = self.plugins_by_name[plugin_name]
        if plugin.results and plugin.results.nested:
            return list(plugin.results.nested)
        return []

    def get_plugin_nested_info(self, plugin_name: str, key: str) -> Any:
        """Get plugin custom nested metadata info."""
        results = self.plugins_by_name[plugin_name].results
        return results.nested[key] if results else {}

    def _load_gateway_config(self):
        if not self.gateway_config_path:
            logger.warning(
                "Gateway path not set; gateway configuration will not be loaded"
            )
            return

        if self.gateway_config is None:
            logger.info("Loading gateway configuration for the first time...")
            self.gateway_config = gateway.GatewayConfig(
                self.gateway_config_path
            )
        else:
            logger.info("Updating gateway configuration...")
            self.gateway_config.update_changed()

        for filename, pvlist in self.gateway_config.pvlists.items():
            if pvlist.hash is not None:
                logger.debug("New gateway file: %s (%s)", filename, pvlist.hash)
                self.container.loaded_files[str(filename)] = pvlist.hash

    def annotate_whatrec(self, ioc: LoadedIoc, what: WhatRecord) -> WhatRecord:
        """
        Annotate WhatRecord instances with things ServerState knows about.
        """
        matches = [
            (what.record.instance if what.record else None),
            what.pva_group
        ]
        for instance in matches:
            if instance is None:
                continue

            if not instance.is_pva:
                # For now, V3 only
                instance.metadata["gateway"] = apischema.serialize(
                    self.get_gateway_matches(instance.name)
                )

                ioc.shell_state.annotate_record(instance)

            for plugin in self.plugins:
                if not plugin.results:
                    continue

                info = list(plugin.results.find_record_metadata(instance.name))
                if info:
                    plugin_key = StringWithContext(plugin.name, context=())
                    instance.metadata[plugin_key] = info

        return what

    def whatrec(self, pvname: str) -> List[WhatRecord]:
        """Find WhatRecord matches."""
        results = []
        for loaded_ioc in self.container.scripts.values():
            what = loaded_ioc.whatrec(pvname)
            if what is not None:
                self.annotate_whatrec(loaded_ioc, what)
                results.append(what)
        return results

    @property
    def aliases(self) -> Dict[str, str]:
        """The CA/V3 aliases."""
        return self.container.aliases

    @property
    def database(self) -> Dict[str, RecordInstance]:
        """The CA/V3 Database of records."""
        return self.container.database

    @property
    def pva_database(self) -> Dict[str, RecordInstance]:
        """The pvAccess Database of groups/records."""
        return self.container.pva_database

    def get_graph(self, pv_names: Tuple[str, ...], graph_type: str) -> graphviz.Digraph:
        if graph_type == "record":
            return self.get_link_graph(tuple(pv_names))
        if graph_type == "script":
            return self.get_script_graph(tuple(pv_names))
        raise RuntimeError("Invalid graph type")

    def get_script_graph(self, pv_names: Tuple[str, ...]) -> graphviz.Digraph:
        if len(pv_names) > settings.MAX_RECORDS:
            raise TooManyRecordsError()

        if not pv_names:
            return graphviz.Digraph()
        gr = graph.graph_script_relations(
            database=self.database,
            limit_to_records=pv_names,
            script_relations=self.script_relations,
        )
        return gr.to_digraph(font_name="Courier")

    def get_ioc_to_pvs(self, pv_names: Tuple[str, ...]) -> Dict[str, List[str]]:
        ioc_to_pvs = {}
        for pv in pv_names:
            try:
                owner = self.container.database[pv].owner or "unknown"
            except KeyError:
                owner = "unknown"

            if owner not in ioc_to_pvs:
                ioc_to_pvs[owner] = []
            ioc_to_pvs[owner].append(pv)
        return ioc_to_pvs

    def get_pv_relations(
        self,
        pv_names: Tuple[str, ...],
        *,
        full: bool = False,
    ) -> Union[PVRelationshipResponse, PVShortRelationshipResponse]:
        # TODO: pv_names
        if full:
            return PVRelationshipResponse(
                pv_relations=self.container.pv_relations,
                script_relations=self.script_relations,
                ioc_to_pvs=self.get_ioc_to_pvs(tuple(self.container.pv_relations))
            )
        return PVShortRelationshipResponse.from_pv_relations(
            pv_relations=self.container.pv_relations,
            script_relations=self.script_relations,
            ioc_to_pvs=self.get_ioc_to_pvs(tuple(self.container.pv_relations))
        )

    def get_link_graph(self, pv_names: Tuple[str, ...]) -> graphviz.Digraph:
        if len(pv_names) > settings.MAX_RECORDS:
            raise TooManyRecordsError()

        if not pv_names:
            return graphviz.Digraph()
        gr = graph.graph_links(
            database=self.database,
            starting_records=list(pv_names),
            sort_fields=True,
            relations=self.container.pv_relations,
        )
        digraph = gr.to_digraph(font_name="Courier")
        return digraph

    def clear_cache(self):
        for method in [
            # self.get_graph,
            # self.get_graph_rendered,
            self.get_gateway_matches,
            self.get_matching_pvs,
            self.get_matching_iocs,
            self.script_info_from_loaded_file,
            self.get_duplicates,
        ]:
            method.cache_clear()

    def is_loaded_file(self, fn) -> bool:
        """Is ``fn`` a file that was loaded?"""
        fn = str(fn)
        if fn in self.container.loaded_files:
            return True

        return any(
            plugin.results.is_loaded_file(fn)
            for plugin in self.plugins
            if plugin.results is not None
        )

    @functools.lru_cache(maxsize=2048)
    def script_info_from_loaded_file(self, fn) -> common.IocshScript:
        with open(fn, "rt") as fp:
            lines = fp.read().splitlines()

        result = []
        for lineno, line in enumerate(lines, 1):
            result.append(
                common.IocshResult(
                    context=(LoadContext(fn, lineno),),
                    line=line,
                )
            )
        return common.IocshScript(path=fn, lines=tuple(result))

    @functools.lru_cache(maxsize=2048)
    def get_gateway_matches(self, pvname: str) -> Optional[gateway.PVListMatches]:
        """Get gateway matches for the given pvname."""
        if self.gateway_config is None:
            return None
        return self.gateway_config.get_matches(pvname)

    @functools.lru_cache(maxsize=20)
    def get_duplicates(
        self, patterns: Tuple[str, ...], use_regex: bool = False
    ) -> Dict[str, List[str]]:
        """Get duplicate PVs from the matching IOC(s)."""
        iocs = self.get_matching_iocs(patterns, use_regex=use_regex)
        seen = collections.defaultdict(list)
        for ioc in iocs:
            shell_state: ShellState = ioc.shell_state
            for record in set(shell_state.database).union(shell_state.pva_database):
                seen[record].append(ioc.name)
        return {
            record: iocs
            for record, iocs in seen.items()
            if len(iocs) > 1
        }

    @functools.lru_cache(maxsize=2048)
    def get_matching_pvs(self, patterns: Tuple[str, ...], use_regex: bool = False) -> List[str]:
        """
        Get matching PV names given pattern(s).

        Parameters
        ----------
        patterns : list of str
            List of patterns in glob or regex format.

        use_regex : bool, optional
            Interpret patterns as glob (False) or regex (True).
        """
        try:
            regex = compile_patterns(patterns, use_regex=use_regex)
        except re.error:
            return []

        pv_names = set(self.database) | set(self.pva_database) | set(self.aliases)
        return [pv_name for pv_name in sorted(pv_names) if regex.match(pv_name)]

    @functools.lru_cache(maxsize=2048)
    def get_matching_iocs(
        self, patterns: Tuple[str, ...], use_regex: bool = False
    ) -> List[LoadedIoc]:
        """
        Get matching IOCs given pattern(s).

        Parameters
        ----------
        patterns : list of str
            List of patterns in glob or regex format.

        use_regex : bool, optional
            Interpret patterns as glob (False) or regex (True).
        """
        try:
            regex = compile_patterns(patterns, use_regex=use_regex)
        except re.error:
            return []

        def by_name(ioc: LoadedIoc):
            return ioc.name

        return [
            loaded_ioc
            for loaded_ioc in sorted(self.container.scripts.values(), key=by_name)
            if regex.match(loaded_ioc.script.path)
            or regex.match(loaded_ioc.metadata.name)
        ]

    async def get_graph_rendered(
        self, pv_names: Tuple[str, ...], format: str, graph_type: str
    ) -> bytes:
        """
        Get a rendered PV relationship graph of the provided PVs.

        Parameters
        ----------
        pv_names : tuple of str
            PV names.

        format : str
            The format of the graph (e.g., pdf, png).

        graph_type : { "record", "script" }
            The type of graph to generate.
        """
        graph = self.get_graph(pv_names, graph_type=graph_type)

        with tempfile.NamedTemporaryFile(suffix=f".{format}") as source_file:
            rendered_filename = await graph.async_render(
                source_file.name, format=format
            )

        with open(rendered_filename, "rb") as fp:
            return fp.read()


class ServerHandler:
    routes = web.RouteTableDef()

    def __init__(self, state: ServerState):
        self.state = state

    async def async_init(self, app):
        await self.state.async_init(app)

    @routes.get("/api/pv/info")
    async def api_pv_get_info(self, request: web.Request):
        pv_names = request.query.getall("pv")
        info = {pv_name: self.state.whatrec(pv_name) for pv_name in pv_names}
        " {pv_name: ..., present: True, info=[whatrec]}"
        return serialized_response(
            {
                pv_name: PVGetInfo(
                    pv_name=pv_name,
                    present=(pv_name in self.state.database or
                             pv_name in self.state.pva_database),
                    info=[obj for obj in info[pv_name]],
                )
                for pv_name in pv_names
            }
        )

    @routes.get("/api/pv/matches")
    async def api_pv_get_matches(self, request: web.Request):
        max_matches = int(request.query.get("max", "200"))
        use_regex, patterns = get_patterns(request.query)
        matches = self.state.get_matching_pvs(patterns, use_regex=use_regex)
        if max_matches > 0:
            matches = matches[:max_matches]

        return serialized_response(
            PVGetMatchesResponse(
                patterns=patterns,
                regex=use_regex,
                matches=matches,
            )
        )

    @routes.get("/api/ioc/matches")
    async def api_ioc_get_matches(self, request: web.Request):
        # Ignore max for now. This is not much in the way of information.
        # max_matches = int(request.query.get("max", "1000"))
        use_regex, patterns = get_patterns(request.query)
        match_metadata = [
            loaded_ioc.metadata
            for loaded_ioc in self.state.get_matching_iocs(patterns, use_regex=use_regex)
        ]
        return serialized_response(
            IocGetMatchesResponse(
                patterns=patterns,
                regex=use_regex,
                matches=match_metadata,
            )
        )

    @routes.get("/api/ioc/pvs")
    async def api_ioc_get_pvs(self, request: web.Request):
        use_regex, ioc_patterns = get_patterns(request.query, key="ioc")
        _, record_patterns = get_patterns(request.query, key="pv")
        response = IocGetMatchingRecordsResponse(
            ioc_patterns=ioc_patterns,
            record_patterns=record_patterns,
            regex=use_regex,
            matches=[],
        )

        def get_all_records(shell_state):
            yield from shell_state.database.items()
            yield from shell_state.pva_database.items()

        try:
            pv_glob_re = compile_patterns(
                record_patterns,
                use_regex=use_regex,
            )
        except re.error:
            raise web.HTTPBadRequest()

        for loaded_ioc in self.state.get_matching_iocs(
            ioc_patterns, use_regex=response.regex,
        ):
            record_matches = [
                rec_info.to_summary()
                for rec, rec_info in sorted(get_all_records(loaded_ioc.shell_state))
                if pv_glob_re.match(rec)
            ]
            if record_matches:
                response.matches.append((loaded_ioc.metadata, record_matches))

        return serialized_response(response)

    @routes.get("/api/pv/duplicates")
    async def api_pv_get_duplicates(self, request: web.Request):
        """Get record names duplicated among two or more IOCs."""
        use_regex, patterns = get_patterns(request.query)
        return serialized_response(
            IocGetDuplicatesResponse(
                patterns=patterns,
                regex=use_regex,
                duplicates=self.state.get_duplicates(patterns, use_regex=use_regex),
            )
        )

    # @routes.get("/api/graphql/query")
    # async def api_graphql_query(self, request: web.Request):
    # TODO: ...

    @routes.get("/api/plugin/info")
    async def api_plugin_info(self, request: web.Request):
        plugins = request.query.get("plugin", "all")
        allow_list = None if plugins == "all" else plugins.split(" ")
        return serialized_response(self.state.get_plugin_info(allow_list))

    @routes.get("/api/plugin/nested/keys")
    async def api_plugin_nested_keys(self, request: web.Request):
        try:
            plugin = request.query["plugin"]
            keys = self.state.get_plugin_nested_keys(plugin)
        except KeyError:
            raise web.HTTPBadRequest()
        return serialized_response(keys)

    @routes.get("/api/plugin/nested/info")
    async def api_plugin_nested_info(self, request: web.Request):
        try:
            plugin = request.query["plugin"]
            key = request.query["key"]
            info = self.state.get_plugin_nested_info(plugin, key)
        except KeyError:
            raise web.HTTPBadRequest()
        return serialized_response(info)

    @routes.get("/api/gateway/info")
    async def api_gateway_info(self, request: web.Request):
        return serialized_response(self.state.gateway_config.pvlists or {})

    @routes.get("/api/file/info")
    async def api_ioc_info(self, request: web.Request):
        # script_name = pathlib.Path(request.query["file"])
        filename = request.query["file"]
        ioc_name = self.state.container.startup_script_to_ioc.get(filename, None)
        if ioc_name:
            loaded_ioc = self.state.container.scripts[ioc_name]
            script_info = loaded_ioc.script
            ioc_md = loaded_ioc.metadata
        else:
            # Making this dual-purpose: script, db, or any loaded file
            ioc_md = None
            if not self.state.is_loaded_file(filename):
                raise web.HTTPBadRequest()

            script_info = self.state.script_info_from_loaded_file(filename)

        return serialized_response(
            {
                "script": script_info,
                "ioc": ioc_md,
            }
        )

    async def get_graph(self, pv_names: List[str], use_glob: bool = False,
                        graph_type: str = "record",
                        format: str = "pdf"):
        if use_glob:
            pv_names = self.state.get_matching_pvs(pv_names)

        if format == "dot":
            try:
                digraph = self.state.get_graph(
                    tuple(pv_names),
                    graph_type=graph_type
                )
            except TooManyRecordsError as ex:
                raise web.HTTPBadRequest() from ex

            return web.Response(
                content_type="text/vnd.graphviz",
                body=digraph.source,
            )

        rendered = await self.state.get_graph_rendered(
            tuple(pv_names), format=format, graph_type=graph_type
        )
        try:
            content_type = {
                "pdf": "application/pdf",
                "png": "image/png",
                "svg": "image/svg+xml",
            }[format]
        except KeyError as ex:
            raise web.HTTPBadRequest() from ex

        return web.Response(
            content_type=content_type,
            body=rendered,
        )

    @routes.get("/api/logs/get")
    async def api_logs_get(self, request: web.Request):
        return web.json_response(
            list(
                _log_handler.messages
                if _log_handler is not None
                else ["Logger not initialized"]
            )
        )

    @routes.get("/api/pv/relations")
    async def api_pv_get_relations(self, request: web.Request):
        use_regex, pv_names = get_patterns(request.query, key="pv")
        pv_names = self.state.get_matching_pvs(pv_names, use_regex=use_regex)
        full = request.query.get("full", "false") in TRUE_VALUES
        return serialized_response(
            self.state.get_pv_relations(pv_names=pv_names, full=full)
        )

    @routes.get("/api/pv/graph")
    async def api_pv_get_record_graph(self, request: web.Request):
        return await self.get_graph(
            pv_names=request.query.getall("pv"),
            use_glob=request.query.get("glob", "false") in TRUE_VALUES,
            format=request.query["format"],
            graph_type="record",
        )

    @routes.get("/api/pv/script-graph")
    async def api_pv_get_script_graph(self, request: web.Request):
        return await self.get_graph(
            pv_names=request.query.getall("pv"),
            use_glob=request.query.get("glob", "false") in TRUE_VALUES,
            format=request.query["format"],
            graph_type="script",
        )


def add_routes(app: web.Application, handler: ServerHandler):
    routes = web.RouteTableDef()
    for route in handler.routes:
        if isinstance(route, web.StaticDef):
            routes.static(route.prefix, route.path, **route.kwargs)
        elif isinstance(route, web.RouteDef):
            method = getattr(handler, route.handler.__name__)
            routes.route(route.method, route.path, **route.kwargs)(method)
    app.router.add_routes(routes)
    return routes


def configure_logging(loggers=None):
    global _log_handler
    _log_handler = ServerLogHandler()

    loggers = loggers or ["whatrecord"]
    for logger_name in loggers:
        logging.getLogger(logger_name).addHandler(_log_handler)


def _new_server_state(
    scripts: Optional[List[str]] = None,
    script_loader: Optional[List[str]] = None,
    archive_management_url: Optional[str] = None,
    gateway_config: Optional[str] = None,
    standin_directory: Optional[Union[List, Dict]] = None,
) -> ServerState:
    """New ServerState from command-line arguments."""
    scripts = scripts or []
    script_loader = script_loader or []

    standin_directory = standin_directory or {}
    if not isinstance(standin_directory, dict):
        standin_directory = dict(path.split("=", 1) for path in standin_directory)

    plugins = []
    if "happi" in settings.PLUGINS:
        plugins.append(
            ServerPluginSpec(
                name="happi",
                module="whatrecord.plugins.happi",
                executable=None,
            )
        )

    if "twincat_pytmc" in settings.PLUGINS:
        plugins.append(
            ServerPluginSpec(
                name="twincat_pytmc",
                module="whatrecord.plugins.twincat_pytmc",
                executable=None,
                after_iocs=True,
            )
        )

    if "netconfig" in settings.PLUGINS:
        plugins.append(
            ServerPluginSpec(
                name="netconfig",
                module="whatrecord.plugins.netconfig",
                executable=None,
                after_iocs=False,
            )
        )

    if "epicsarch" in settings.PLUGINS:
        plugins.append(
            ServerPluginSpec(
                name="epicsarch",
                module="whatrecord.plugins.epicsarch",
                executable=None,
                after_iocs=False,
            )
        )

    return ServerState(
        startup_scripts=scripts,
        script_loaders=script_loader,
        standin_directories=standin_directory,
        gateway_config=gateway_config,
        plugins=plugins,
    )


def _new_server(
    handler: ServerHandler,
    port: int,
    run: bool = False,
) -> web.Application:
    """Create a new aiohttp Application for the given ServerHandler."""
    app = web.Application()
    add_routes(app, handler)

    # Set the environment variable for plugins or subprocesses to be able to
    # query the server.
    os.environ["WHATRECORD_SERVER"] = f"http://localhost:{port}"

    app.on_startup.append(handler.async_init)
    if run:
        try:
            web.run_app(app, port=port)
        except KeyboardInterrupt:
            ...
    return app


def main(
    scripts: Optional[List[str]] = None,
    script_loader: Optional[List[str]] = None,
    archive_management_url: Optional[str] = None,
    gateway_config: Optional[str] = None,
    standin_directory: Optional[Union[List, Dict]] = None,
    port: int = 8898,
    use_tracemalloc: bool = False,
    offline_dump_target: Optional[AnyPath] = None,
    start: bool = True,
):
    if use_tracemalloc and tracemalloc is not None:
        tracemalloc.start()

    configure_logging()

    state: ServerState = _new_server_state(
        scripts=scripts,
        script_loader=script_loader,
        archive_management_url=archive_management_url,
        gateway_config=gateway_config,
        standin_directory=standin_directory,
    )
    handler = ServerHandler(state)
    if offline_dump_target is None:
        # Run the server.
        run = True
    else:
        # Dump to a file - but don't start the server.
        asyncio.run(state.trigger_manual_update())
        asyncio.run(state.dump_to_file(pathlib.Path(offline_dump_target)))
        run = False

    app = _new_server(handler, port=port, run=run)

    if use_tracemalloc and tracemalloc is not None:
        global tracemalloc_snapshot
        tracemalloc_snapshot = tracemalloc.take_snapshot()

    return app, handler
