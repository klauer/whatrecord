<template>
  <h3>{{ whatrec.name }}</h3>
  <!-- Available in {{ available_protocols }} -->
  <template
    v-for="([defn, instance], idx) in [
      [record_defn, record],
      [null, pva_group],
    ]"
    :key="idx"
  >
    <template v-if="instance != null">
      <script-context-link
        :context="instance.context"
        :short="0"
      ></script-context-link>
      <br />
      <epics-format-record
        :name="instance.name"
        :aliases="instance.aliases"
        :context="instance.context"
        :fields="instance.fields"
        :record_type="instance.record_type"
        :is_grecord="instance.is_grecord"
        :is_pva="instance.is_pva"
        :info_nodes="instance.info"
        :metadata="instance.metadata"
        :record_defn="defn"
        :menus="whatrec.menus"
      />

      <template v-for="plugin in plugins" :key="plugin.name">
        <template
          v-for="plugin_match in instance.metadata[plugin.name] || []"
          :key="plugin_match"
        >
          <details>
            <summary>
              {{ plugin.name }} - {{ plugin_match.name }}
              <router-link :to="`/${plugin.name}/${plugin_match.name}`">
                (Details)
              </router-link>
            </summary>
            <dictionary-table
              :dict="plugin_match"
              :cls="'metadata'"
              :skip_keys="['_whatrecord']"
            />
          </details>
          <br />
        </template>
      </template>
    </template>
  </template>

  <template v-if="streamdevice_metadata">
    <details>
      <summary>
        StreamDevice protocol (
        <span class="monospace">
          {{ streamdevice_metadata.protocol_file }} </span
        >,
        <span class="monospace">
          "{{ streamdevice_metadata.protocol_name }}"
        </span>
        )
      </summary>
      <dictionary-table
        :dict="streamdevice_metadata"
        :cls="'metadata'"
        :skip_keys="['protocol']"
      />
      <template v-if="'protocol' in streamdevice_metadata">
        <br />Protocol:
        <dictionary-table
          :dict="streamdevice_metadata.protocol"
          :cls="'metadata'"
          :skip_keys="['commands']"
        />
        <br />Commands:
        <br />
        <span class="code">
          <template
            v-for="command in streamdevice_metadata.protocol.commands"
            :key="command.name"
          >
            {{ command.name }} {{ command.arguments.join(" ") }}<br />
          </template>
        </span>
      </template>
    </details>
    <br />
  </template>

  <Accordion :multiple="true" class="accordion">
    <AccordionTab :header="`Part of ${whatrec.ioc.name}`">
      <ioc-info :ioc_info="whatrec.ioc" />
    </AccordionTab>
    <AccordionTab header="Record links">
      <a :href="graph_link" v-if="graph_link" target="_blank">
        <img class="svg-graph" :src="graph_link" />
      </a>
      <div id="d3-rendered-graph" />
    </AccordionTab>
    <AccordionTab header="Archiver" v-if="record != null">
      <template v-if="record != null && appliance_viewer_url">
        <a :href="appliance_viewer_url" target="_blank"> Archive Viewer </a>
        <iframe :src="appliance_viewer_url" title="Archive viewer" />
      </template>
    </AccordionTab>
    <AccordionTab header="Autosave" v-if="record && autosave != null">
      <template v-if="autosave?.disconnected">
        Disconnected fields:
        <ul>
          <li v-for="field in autosave.disconnected" :key="field">
            {{ field }}
          </li>
        </ul>
      </template>
      <template v-for="(table, idx) in autosave_restore_tables" :key="idx">
        <DataTable :value="Object.values(table)" dataKey="name">
          <Column
            field="field"
            header="Field"
            :sortable="true"
            style="width: 10%"
          />
          <Column field="value" header="Value" :sortable="true" />
          <Column field="context" header="Context" :sortable="true">
            <template #body="{ data }">
              <script-context-link :context="data.context" :short="3" />
            </template>
          </Column>
        </DataTable>
      </template>
      <template v-if="autosave.error">
        Autosave errors on {{ whatrec.ioc.name }}:
        <template v-for="error of autosave.error ?? []" :key="error">
          <pre>{{ error }}</pre>
        </template>
      </template>
    </AccordionTab>
    <AccordionTab
      header="Gateway"
      v-if="record?.metadata?.gateway?.matches?.length > 0"
    >
      <gateway-matches :matches="record.metadata.gateway.matches" />
    </AccordionTab>
    <AccordionTab header="Access Security Group" v-if="asg != null">
      <dictionary-table :dict="asg" :cls="'metadata'" :skip_keys="[]" />
    </AccordionTab>
    <AccordionTab header="Asyn" v-if="record?.metadata?.asyn?.length > 0">
      <asyn-port
        v-for:="(asyn_port, idx) in record.metadata.asyn"
        :asyn_port="asyn_port"
        :key="asyn_port.name"
      />
    </AccordionTab>
    <AccordionTab header="Field table">
      <template v-if="record != null">
        <b v-if="pva_group != null">Channel Access (V3)</b>
        <record-field-table :fields="record.fields" :pva="false" />
      </template>
      <template v-if="pva_group != null">
        <b v-if="record != null">PVAccess</b>
        <record-field-table :fields="pva_group.fields" :pva="true" />
      </template>
    </AccordionTab>
    <AccordionTab header="Raw information">
      <pre>{{ whatrec }}</pre>
    </AccordionTab>
  </Accordion>
</template>

<script>
import AsynPort from "./asyn-port.vue";
import DictionaryTable from "./dictionary-table.vue";
import EpicsFormatRecord from "./epics-format-record.vue";
import GatewayMatches from "./gateway-matches.vue";
import IocInfo from "./ioc-info.vue";
import RecordFieldTable from "./record-field-table.vue";
import ScriptContextLink from "./script-context-link.vue";

import Accordion from "primevue/accordion";
import AccordionTab from "primevue/accordiontab";
import Column from "primevue/column";
import DataTable from "primevue/datatable";

import { plugins } from "../settings.js";

import { graphviz } from "d3-graphviz";

export default {
  name: "Recordinfo",
  props: {
    whatrec: Object,
  },
  components: {
    AsynPort,
    Column,
    DataTable,
    DictionaryTable,
    EpicsFormatRecord,
    GatewayMatches,
    IocInfo,
    RecordFieldTable,
    ScriptContextLink,
    Accordion,
    AccordionTab,
  },
  computed: {
    appliance_viewer_url() {
      const appliance_viewer_url =
        import.meta.env.WHATRECORD_ARCHIVER_URL || "";
      if (!appliance_viewer_url || !this.record) {
        return null;
      }
      return appliance_viewer_url + this.record.name;
    },
    graph_link() {
      return "";
      // TODO uncomment
      // return `/api/pv/graph?pv=${this.whatrec.name}&format=svg`;
    },
    script_graph_link() {
      return `/api/pv/script-graph?pv=${this.whatrec.name}&format=svg`;
    },
    available_protocols() {
      let protocols = [];
      if (this.record != null) {
        protocols.push("Channel Access (CA)");
      }
      if (this.pva_group != null) {
        protocols.push("PVAccess");
      }
      return protocols.join(", ");
    },
    pva_group() {
      return this.whatrec.pva_group;
    },
    record() {
      return this.whatrec.record ? this.whatrec.record.instance : null;
    },
    pv_relation_dot_source() {
      const pv_relations =
        this.$store.state.cache?.pv_relations[this.record?.name] ?? null;
      return pv_relations ? pv_relations.source : null;
    },
    record_defn() {
      return this.whatrec.record ? this.whatrec.record.definition : null;
    },
    asg() {
      return this.record?.metadata["asg"];
    },
    autosave() {
      return this.record?.metadata["autosave"] ?? null;
    },
    autosave_restore_tables() {
      return this.autosave?.restore ?? [];
    },
    streamdevice_metadata() {
      return this.record?.metadata["streamdevice"];
    },
    plugins() {
      return plugins;
    },
  },
  async created() {
    this.$watch(
      () => this.$route.params,
      (to_params) => {
        this.from_route(to_params, this.$route.query);
      }
    );
    await this.from_route(this.$route.params, this.$route.query);
  },
  async mounted() {
    const graph = graphviz("#d3-rendered-graph");
    graph.scale(0.5);
    graph.height(600);
    graph.renderDot(this.pv_relation_dot_source);
  },
  methods: {
    async from_route(params, query) {
      console.log("from route", params, query);
    },
  },
};
</script>

<style scoped>
.svg-graph {
  max-width: 70%;
}

.d3-rendered-graph {
  max-width: 70%;
  max-height: 50%;
}

.monospace {
  font-family: monospace;
}

.p-panel {
  padding-bottom: 1em;
}

iframe {
  min-height: 450px;
  width: 100%;
}

.accordion {
  max-width: 76vw;
}

.code {
  background: var(--surface-f);
  border-left: 3px solid var(--surface-600);
  color: var(--text-color);
  display: block;
  font-family: monospace;
  font-size: 15px;
  line-height: 1;
  margin-bottom: 1.6em;
  max-width: 100%;
  overflow: auto;
  padding: 15px;
  page-break-inside: avoid;
  word-wrap: break-word;
}
</style>
