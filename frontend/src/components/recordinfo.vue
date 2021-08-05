<template>
  <h3> {{ whatrec.name }} </h3>
  <!-- Available in {{ available_protocols }} -->
  <template v-for="([defn, instance], idx) in [[record_defn, record], [null, pva_group]]" :key="idx">
    <template v-if="instance != null">
      <script-context-link :context="instance.context" :short=false></script-context-link>
      <br/>
      <epics-format-record
        :name="instance.name"
        :aliases="instance.aliases"
        :context="instance.context"
        :fields="instance.fields"
        :record_type="instance.record_type"
        :is_grecord="instance.is_grecord"
        :is_pva="instance.is_pva"
        :metadata="instance.metadata"
        :record_defn="defn"
        :menus="whatrec.menus"
      />

      <template v-for="plugin_name in plugins" :key="plugin_name">
        <template v-for="plugin_match in instance.metadata[plugin_name] || []" :key="plugin_match">
          <details>
            <summary>
              {{ plugin_name }} - {{ plugin_match.name }}
              <router-link :to="`/${plugin_name}/${plugin_match.name}`">
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
            {{ streamdevice_metadata.protocol_file }}
          </span>,
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
          <template v-for="command in streamdevice_metadata.protocol.commands" :key="command.name">
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
      <a :href="graph_link" target="_blank">
        <img class="svg-graph" :src="graph_link" />
      </a>
    </AccordionTab>
    <AccordionTab header="Archiver" v-if="record != null">
      <template v-if="record != null && appliance_viewer_url">
        <a :href="appliance_viewer_url" target="_blank">
          Archive Viewer
        </a>
        <iframe
          :src="appliance_viewer_url"
          title="Archive viewer"
          />
        </template>
    </AccordionTab>
    <AccordionTab header="Gateway" v-if="record != null">
      <template v-if="record != null && record.metadata.gateway != null && record.metadata.gateway.matches.length > 0">
        Matching gateway rules:
        <gateway-matches :matches="record.metadata.gateway.matches"/>
      </template>
      <template v-else>
        No matches with gateway rules.
      </template>
    </AccordionTab>
    <AccordionTab header="Access Security Group" v-if="asg != null">
      <dictionary-table
        :dict="asg"
        :cls="'metadata'"
        :skip_keys="[]"
      />
    </AccordionTab>
    <AccordionTab header="Asyn" v-if="whatrec.asyn_ports.length > 0">
      <asyn-port
        v-for:="(asyn_port, idx) in whatrec.asyn_ports"
        :asyn_port="asyn_port"
        :key="asyn_port.name"/>
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
      <pre>{{whatrec}}</pre>
    </AccordionTab>
  </Accordion>
</template>

<script>
import AsynPort from './asyn-port.vue'
import DictionaryTable from './dictionary-table.vue'
import EpicsFormatRecord from './epics-format-record.vue'
import GatewayMatches from './gateway-matches.vue'
import IocInfo from './ioc-info.vue'
import RecordFieldTable from './record-field-table.vue'
import ScriptContextLink from './script-context-link.vue'
import Accordion from 'primevue/accordion';
import AccordionTab from 'primevue/accordiontab';

import { plugins } from '../settings.js';


export default {
  name: 'Recordinfo',
  props: {
    whatrec: Object,
  },
  components: {
    AsynPort,
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
      const appliance_viewer_url = process.env.WHATRECORD_ARCHIVER_URL || "";
      if (!appliance_viewer_url || !this.record) {
          return null;
      }
      return appliance_viewer_url + this.record.name;
    },
    graph_link() {
      return "/api/pv/" + this.whatrec.name + "/graph/svg";
    },
    script_graph_link() {
      return "/api/pv/" + this.whatrec.name + "/script-graph/svg";
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
    record_defn() {
      return this.whatrec.record ? this.whatrec.record.definition : null;
    },
    asg() {
      if (this.record == null) {
        return null;
      }
      return this.record.metadata["asg"];
    },
    streamdevice_metadata() {
      if (this.record == null) {
        return null;
      }
      return this.record.metadata["streamdevice"];
    },
    happi_metadata() {
      if (this.record == null) {
        return [];
      }
      return this.record.metadata["happi"] || [];
    },
    plugins() {
      return plugins;
    }
  }
}
</script>

<style scoped>
.svg-graph {
  max-width: 70%;
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
  max-width: 80vw;
}

.code {
  background: var(--surface-f);
  border-left: 3px solid var(--surface-600);
  color: var(--text-color);
  display: block;
  font-family: monospace;
  font-size: 15px;
  line-height: 1.0;
  margin-bottom: 1.6em;
  max-width: 100%;
  overflow: auto;
  padding: 15px;
  page-break-inside: avoid;
  word-wrap: break-word;
}
</style>
