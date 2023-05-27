import axios from "axios";
import "es6-promise/auto";
import { createStore } from "vuex";
import wcmatch from "wildcard-match";

export const remote_store = createStore({
  state: () => ({
    duplicates: {},
    file_info: {},
    gateway_info: null,
    glob_to_pvs: {},
    ioc_info: [],
    ioc_to_records: {},
    plugin_info: {},
    plugin_nested_info: {},
    pv_relations: {},
    queries_in_progress: 0,
    query_in_progress: false,
    record_info: {},
    regex_to_pvs: {},
  }),
  mutations: {
    start_query(state) {
      state.queries_in_progress += 1;
      state.query_in_progress = true;
    },
    end_query(state) {
      if (state.queries_in_progress > 0) {
        state.queries_in_progress -= 1;
      }
      if (state.queries_in_progress === 0) {
        state.query_in_progress = false;
      }
    },
    add_record_search_results(state, { pattern, pv_list, regex }) {
      if (regex) {
        state.regex_to_pvs[pattern] = pv_list;
      } else {
        state.glob_to_pvs[pattern] = pv_list;
      }
    },
    set_duplicates(state, { duplicates }) {
      state.duplicates = duplicates;
    },
    set_file_info(state, { filename, info }) {
      state.file_info[filename] = info;
    },
    set_ioc_info(state, { ioc_info }) {
      state.ioc_info = ioc_info;
    },
    set_gateway_info(state, { gateway_info }) {
      state.gateway_info = gateway_info;
    },
    set_plugin_info(state, { plugin_name, plugin_info }) {
      state.plugin_info[plugin_name] = plugin_info;
    },
    set_plugin_nested_keys(state, { plugin_name, keys }) {
      if (plugin_name in state.plugin_nested_info === false) {
        state.plugin_nested_info[plugin_name] = {
          keys: null,
          info: {},
        };
      }
      state.plugin_nested_info[plugin_name].keys = keys;
    },
    set_plugin_nested_info(state, { plugin_name, key, info }) {
      if (plugin_name in state.plugin_nested_info === false) {
        state.plugin_nested_info[plugin_name] = {
          keys: [],
          info: {},
        };
      }
      state.plugin_nested_info[plugin_name].info[key] = info;
    },
    add_record_info(state, { record, info }) {
      state.record_info[record] = info;
    },
    set_ioc_records(state, { ioc_name, records }) {
      state.ioc_to_records[ioc_name] = records;
    },
    set_pv_relations(state, { data }) {
      state.pv_relations = data;
    },
  },
  actions: {
    async update_ioc_info(context) {
      if (context.state.ioc_info.length > 0) {
        return context.state.ioc_info;
      }
      try {
        await context.commit("start_query");
        const response = await axios.get("/api/ioc/matches", {
          params: { pattern: "*" },
        });
        await context.commit("set_ioc_info", {
          ioc_info: response.data.matches,
        });
        return response.data.matches;
      } catch (error) {
        console.error(error);
      } finally {
        await context.commit("end_query");
      }
    },

    async update_gateway_info(context) {
      try {
        await context.commit("start_query");
        const response = await axios.get("/api/gateway/info", {});
        await context.commit("set_gateway_info", {
          gateway_info: response.data,
        });
        return response.data;
      } catch (error) {
        console.error(error);
      } finally {
        await context.commit("end_query");
      }
    },

    async update_plugin_info(context, { plugin }) {
      try {
        await context.commit("start_query");
        const response = await axios.get("/api/plugin/info", {
          params: {
            plugin: plugin,
          },
        });
        await context.commit("set_plugin_info", {
          plugin_info: response.data[plugin],
          plugin_name: plugin,
        });
        return response.data;
      } catch (error) {
        console.error(error);
      } finally {
        await context.commit("end_query");
      }
    },

    async get_plugin_nested_keys(context, { plugin }) {
      try {
        await context.commit("start_query");
        const response = await axios.get("/api/plugin/nested/keys", {
          params: {
            plugin: plugin,
          },
        });
        await context.commit("set_plugin_nested_keys", {
          plugin_name: plugin,
          keys: response.data,
        });
        return response.data;
      } catch (error) {
        console.error(error);
      } finally {
        await context.commit("end_query");
      }
    },

    async get_plugin_nested_info(context, { plugin, key }) {
      try {
        await context.commit("start_query");
        const response = await axios.get("/api/plugin/nested/info", {
          params: {
            plugin: plugin,
            key: key,
          },
        });
        await context.commit("set_plugin_nested_info", {
          plugin_name: plugin,
          key: key,
          info: response.data,
        });
        return response.data;
      } catch (error) {
        console.error(error);
      } finally {
        await context.commit("end_query");
      }
    },

    async get_record_info(context) {
      if (record_name in context.state.record_info) {
        return context.state.record_info[record_name];
      }
      try {
        await context.commit("start_query");
        const response = await axios.get("/api/pv/info", {
          params: { pv: record_name },
        });
        for (const [rec, rec_info] of Object.entries(response.data)) {
          await context.commit("add_record_info", {
            record: rec,
            info: rec_info,
          });
        }
        return response.data;
      } catch (error) {
        console.error(error);
      } finally {
        await context.commit("end_query");
      }
    },

    async get_ioc_records(context, { ioc_name }) {
      try {
        await context.commit("start_query");
        const response = await axios.get("/api/ioc/pvs", {
          params: { ioc: ioc_name, pv: "*" },
        });
        const records =
          response.data.matches.length > 0 ? response.data.matches[0][1] : [];
        await context.commit("set_ioc_records", {
          ioc_name: ioc_name,
          records: records,
        });
        return records;
      } catch (error) {
        console.error(error);
      } finally {
        await context.commit("end_query");
      }
    },

    async get_file_info(context, { filename }) {
      try {
        await context.commit("start_query");
        const response = await axios.get("/api/file/info", {
          params: {
            file: filename,
          },
        });
        await context.commit("set_file_info", {
          filename: filename,
          info: response.data,
        });
        return response.data;
      } catch (error) {
        console.error(error);
      } finally {
        await context.commit("end_query");
      }
    },

    async update_duplicates(context) {
      try {
        await context.commit("start_query");
        const response = await axios.get("/api/pv/duplicates", {
          params: {
            pattern: "*",
          },
        });
        await context.commit("set_duplicates", {
          duplicates: response.data.duplicates,
        });
        return response.data;
      } catch (error) {
        console.error(error);
      } finally {
        await context.commit("end_query");
      }
    },

    async get_pv_relations(context) {
      const full = false;
      const pv_glob = "*";

      try {
        await context.commit("start_query");
        const response = await axios.get("/api/pv/relations", {
          params: {
            pv: pv_glob,
            glob: true,
            full: full,
          },
        });
        await context.commit("set_pv_relations", { data: response.data });
        return response.data;
      } catch (error) {
        console.error(error);
      } finally {
        await context.commit("end_query");
      }
    },

    async find_record_matches(context, { pattern, max_pvs, regex }) {
      if (pattern == null) {
        return;
      }
      if (!regex && pattern in context.state.glob_to_pvs) {
        return context.state.glob_to_pvs[pattern];
      } else if (regex && pattern in context.state.regex_to_pvs) {
        return context.state.regex_to_pvs[pattern];
      }
      await context.commit("start_query");
      const query_pattern = pattern || (regex ? ".*" : "*");
      console.debug(
        "Search for PV matches:",
        query_pattern,
        regex ? "regex" : "glob"
      );

      try {
        const response = await axios.get("/api/pv/matches", {
          params: { pattern: query_pattern, max: max_pvs, regex: regex },
        });
        const matches = response.data["matches"];
        await context.commit("add_record_search_results", {
          pattern: pattern,
          max_pvs: max_pvs,
          pv_list: matches,
          regex: regex,
        });
        return matches;
      } catch (error) {
        console.error("Failed to get PV list from glob", error);
      } finally {
        await context.commit("end_query");
      }
    },
  },
});

export const cached_local_store = createStore({
  state: () => ({
    cache: null,
    file_stream: null,
    duplicates: {},
    file_info: {},
    gateway_info: null,
    glob_to_pvs: {},
    ioc_info: [],
    ioc_to_records: {},
    plugin_info: {},
    plugin_nested_info: {},
    pv_relations: {},
    queries_in_progress: 0,
    query_in_progress: false,
    record_info: {},
    regex_to_pvs: {},
  }),
  mutations: {
    start_query(state) {
      state.queries_in_progress += 1;
      state.query_in_progress = true;
    },
    end_query(state) {
      if (state.queries_in_progress > 0) {
        state.queries_in_progress -= 1;
      }
      if (state.queries_in_progress === 0) {
        state.query_in_progress = false;
      }
    },
    add_record_search_results(state, { pattern, pv_list, regex }) {
      if (regex) {
        state.regex_to_pvs[pattern] = pv_list;
      } else {
        state.glob_to_pvs[pattern] = pv_list;
      }
    },
    set_cache(state, { cache }) {
      console.log("Got cached whatrecord information from server", cache);
      state.cache = cache;
    },
    set_duplicates(state, { duplicates }) {
      state.duplicates = duplicates;
    },
    set_file_info(state, { filename, info }) {
      state.file_info[filename] = info;
    },
    set_ioc_info(state, { ioc_info }) {
      state.ioc_info = ioc_info;
    },
    set_gateway_info(state, { gateway_info }) {
      state.gateway_info = gateway_info;
    },
    set_plugin_info(state, { plugin_name, plugin_info }) {
      state.plugin_info[plugin_name] = plugin_info;
    },
    set_plugin_nested_keys(state, { plugin_name, keys }) {
      if (plugin_name in state.plugin_nested_info === false) {
        state.plugin_nested_info[plugin_name] = {
          keys: null,
          info: {},
        };
      }
      state.plugin_nested_info[plugin_name].keys = keys;
    },
    set_plugin_nested_info(state, { plugin_name, key, info }) {
      if (plugin_name in state.plugin_nested_info === false) {
        state.plugin_nested_info[plugin_name] = {
          keys: [],
          info: {},
        };
      }
      state.plugin_nested_info[plugin_name].info[key] = info;
    },
    add_record_info(state, { record, info }) {
      state.record_info[record] = info;
    },
    set_ioc_records(state, { ioc_name, records }) {
      state.ioc_to_records[ioc_name] = records;
    },
    set_pv_relations(state, { data }) {
      state.pv_relations = data;
    },
  },
  actions: {
    async load_cached_whatrecord_data(context) {
      if (context.state.cache != null) {
        return context.state.cache;
      }
      try {
        await context.commit("start_query");
        const response = await axios.get("/cache.json.gz", {
          decompress: true,
          headers: {
            "Content-Type": "application/json",
            Accept: "application/gzip",
          },
        });
        await context.commit("set_cache", { cache: response.data });
        return response.data;
      } catch (error) {
        console.error(error);
        return {};
      } finally {
        await context.commit("end_query");
      }
    },

    async update_ioc_info(context) {
      if (context.state.ioc_info.length > 0) {
        return context.state.ioc_info;
      }
      const cache = await context.dispatch("load_cached_whatrecord_data");
      const ioc_info = Object.values(cache.iocs).map((ioc) => ioc["md"]);
      await context.commit("set_ioc_info", { ioc_info: ioc_info });
    },

    async update_gateway_info(context) {
      try {
        await context.commit("start_query");
        const response = await axios.get("/api/gateway/info", {});
        await context.commit("set_gateway_info", {
          gateway_info: response.data,
        });
        return response.data;
      } catch (error) {
        console.error(error);
      } finally {
        await context.commit("end_query");
      }
    },

    async update_plugin_info(context, { plugin }) {
      try {
        await context.commit("start_query");
        const response = await axios.get("/api/plugin/info", {
          params: {
            plugin: plugin,
          },
        });
        await context.commit("set_plugin_info", {
          plugin_info: response.data[plugin],
          plugin_name: plugin,
        });
        return response.data;
      } catch (error) {
        console.error(error);
      } finally {
        await context.commit("end_query");
      }
    },

    async get_plugin_nested_keys(context, { plugin }) {
      try {
        await context.commit("start_query");
        const response = await axios.get("/api/plugin/nested/keys", {
          params: {
            plugin: plugin,
          },
        });
        await context.commit("set_plugin_nested_keys", {
          plugin_name: plugin,
          keys: response.data,
        });
        return response.data;
      } catch (error) {
        console.error(error);
      } finally {
        await context.commit("end_query");
      }
    },

    async get_plugin_nested_info(context, { plugin, key }) {
      try {
        await context.commit("start_query");
        const response = await axios.get("/api/plugin/nested/info", {
          params: {
            plugin: plugin,
            key: key,
          },
        });
        await context.commit("set_plugin_nested_info", {
          plugin_name: plugin,
          key: key,
          info: response.data,
        });
        return response.data;
      } catch (error) {
        console.error(error);
      } finally {
        await context.commit("end_query");
      }
    },

    async get_record_info(context, { record_name }) {
      if (record_name in context.state.record_info) {
        return context.state.record_info[record_name];
      }
      const cache = await context.dispatch("load_cached_whatrecord_data");

      let matches = [];
      for (const ioc_info of Object.values(cache.iocs)) {
        const ioc = ioc_info.ioc; // .md
        const v3_record = ioc.shell_state.database[record_name];
        if (v3_record != null) {
          // TODO: this squashes duplicates
          let info = {};
          const whatrec = {
            name: record_name,
            ioc: ioc_info.md,
            record: {
              definition:
                ioc.shell_state.database_definition?.record_types[
                  v3_record.record_type
                ] ?? null,
              instance: v3_record,
            },
            pva_group: null,
          };
          // info[record_name] = {"pv_name": record_name, present: true, "info": [whatrec]};
          // TODO: Why is this so convoluted? refactor this!
          info = { pv_name: record_name, present: true, info: [whatrec] };
          console.log("whatrec", info[record_name]);
          await context.commit("add_record_info", {
            record: record_name,
            info: info,
          });
        }

        const v4_record = ioc.shell_state.pva_database[record_name];
        if (v4_record != null) {
          // TODO: this squashes duplicates
          let info = {};
          const whatrec = {
            name: record_name,
            ioc: ioc_info.md,
            record: {
              definition: null,
              instance: null,
            },
            pva_group: v4_record,
          };
          // info[record_name] = {"pv_name": record_name, present: true, "info": [whatrec]};
          // TODO: Why is this so convoluted? refactor this!
          info = { pv_name: record_name, present: true, info: [whatrec] };
          console.log("whatrec", info[record_name]);
          await context.commit("add_record_info", {
            record: record_name,
            info: info,
          });
        }
      }
    },

    async get_ioc_records(context, { ioc_name }) {
      const cache = await context.dispatch("load_cached_whatrecord_data");
      const v3_records = Object.values(
        cache.iocs[ioc_name].ioc.shell_state.database
      );
      const v4_records = Object.values(
        cache.iocs[ioc_name].ioc.shell_state.pva_database
      );
      const records = [].concat(v3_records, v4_records);
      await context.commit("set_ioc_records", {
        ioc_name: ioc_name,
        records: records,
      });
      return records;
    },

    async get_file_info(context, { filename }) {
      if (filename in context.state.file_info) {
        return context.state.file_info[filename];
      }
      const cache = await context.dispatch("load_cached_whatrecord_data");
      const cached_file_info = cache.files[filename];
      if (cached_file_info?.script) {
        await context.commit("set_file_info", {
          filename: filename,
          info: cached_file_info,
        });
      } else if (cached_file_info?.lines) {
        const line_by_line_info = cached_file_info.lines.map(
          (line, lineno) => ({
            context: [[filename, lineno + 1]],
            line: line,
          })
        );
        const script = { path: filename, lines: line_by_line_info };
        await context.commit("set_file_info", {
          filename: filename,
          info: { script: script, ioc: null },
        });
      }
    },

    async update_duplicates(context) {
      if (Object.keys(context.state.duplicates).length) {
        return context.state.duplicates;
      }

      const cache = await context.dispatch("load_cached_whatrecord_data");
      let dupes = {};
      for (const [iocname, ioc_info] of Object.entries(cache.iocs)) {
        for (const pvname of Object.keys(ioc_info.ioc.shell_state.database)) {
          if (pvname in dupes === false) {
            dupes[pvname] = [];
          }
          dupes[pvname].push(iocname);
        }
        for (const pvname of Object.keys(
          ioc_info.ioc.shell_state.pva_database
        )) {
          if (pvname in dupes === false) {
            dupes[pvname] = [];
          }
          dupes[pvname].push(iocname);
        }
      }
      for (const [pvname, iocs] of Object.entries(dupes)) {
        if (iocs.length <= 1) {
          delete dupes[pvname];
        }
      }
      await context.commit("set_duplicates", {
        duplicates: dupes,
      });
    },

    async get_pv_relations(context) {
      const full = false;
      const pv_glob = "*";

      try {
        await context.commit("start_query");
        const response = await axios.get("/api/pv/relations", {
          params: {
            pv: pv_glob,
            glob: true,
            full: full,
          },
        });
        await context.commit("set_pv_relations", { data: response.data });
        return response.data;
      } catch (error) {
        console.error(error);
      } finally {
        await context.commit("end_query");
      }
    },

    async find_record_matches(context, { pattern, max_pvs, regex }) {
      if (pattern == null) {
        return;
      }
      if (!regex && pattern in context.state.glob_to_pvs) {
        return context.state.glob_to_pvs[pattern];
      } else if (regex && pattern in context.state.regex_to_pvs) {
        return context.state.regex_to_pvs[pattern];
      }

      const query_pattern = pattern || (regex ? ".*" : "*");
      console.debug(
        "Search for PV matches:",
        query_pattern,
        regex ? "regex" : "glob"
      );

      const cache = await context.dispatch("load_cached_whatrecord_data");

      let matcher = null;
      if (regex) {
        try {
          const re = new RegExp(pattern);
          // re goes out of scope otherwise:
          matcher = re.test.bind(re);
        } catch (e) {
          console.warn("Invalid regular expression:", pattern, e);
          return;
        }
      } else {
        matcher = wcmatch(pattern);
      }

      let matches = [];
      for (const ioc_info of Object.values(cache.iocs)) {
        const ioc = ioc_info.ioc; // .md
        for (const [name, record] of Object.entries(ioc.shell_state.database)) {
          if (matcher(name)) {
            matches.push(name);
          }
        }
        for (const [name, record] of Object.entries(
          ioc.shell_state.pva_database
        )) {
          if (matcher(name)) {
            matches.push(name);
          }
        }
      }

      try {
        await context.commit("add_record_search_results", {
          pattern: pattern,
          max_pvs: max_pvs,
          pv_list: Array.from(new Set(matches)).sort(),
          regex: regex,
        });
        return matches;
      } catch (error) {
        console.error("Failed to get PV list from glob", error);
      } finally {
        await context.commit("end_query");
      }
    },
  },
});

export const store = cached_local_store;
