<template>
  <template v-if="item_name">
    <h2>{{ item_name }} - Metadata</h2>
    <dictionary-table
      :dict="happi_item_info"
      :cls="'metadata'"
      :skip_keys="['_whatrecord']"
      />

    <h2>{{ item_name }} - Related Records</h2>
    <template v-if="Object.keys(kind_to_related_records).length > 0">
      <table id="related_records">
        <thead>
          <th>PV</th>
          <th>Attribute</th>
          <th>Kind</th>
        </thead>
        <tbody>
          <template v-for="[kind, records] in Object.entries(kind_to_related_records)" :key="kind">
            <tr v-for="rec in records" :key="rec.name">
              <td class="pv">
                <router-link :to="`/whatrec/${rec.name}/${rec.name}`">
                  {{rec.name}}
                </router-link>
              </td>
              <td class="pv">
                {{ rec.signal }}
              </td>
              <td class="pv">
                {{ kind.replaceAll("Kind.", "") }}
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </template>
  </template>
  <template v-else>
    <DataTable
        id="happi_table"
        :value="happi_items"
        dataKey="name"
        filterDisplay="row"
        v-model:filters="filters"
        :globalFilterFields="global_filter_fields"
    >
      <template #header>
        <div class="p-d-flex p-jc-between">
          <MultiSelect
            :modelValue="selected_columns"
            :options="columns" optionLabel="header"
            @update:modelValue="onToggle"
            placeholder="Select Columns"
            style="width: 20em"
          />

          <Button type="button" icon="pi pi-filter-slash" label="Clear"
            class="p-button-outlined" @click="clear_filters()"/>
          <span class="p-input-icon-left">
            <i class="pi pi-search" />
            <InputText v-model="filters['global'].value" placeholder="Search" />
          </span>
        </div>
      </template>
      <Column field="name" header="Name" :sortable="true">
        <template #body="{data}">
          <router-link :to="`/happi/${data.name}`">{{data.name}}</router-link>
        </template>
        <template #filter="{filterModel,filterCallback}">
          <InputText type="text" v-model="filterModel.value" @keydown.enter="filterCallback()" class="p-column-filter"
            :placeholder="`Filter by name`" />
        </template>
      </Column>
      <Column field="device_class" header="Class"> :sortable="true"
        <template #body="{data}">
          <div class="tooltip">
            {{ data.device_class.split(".").slice(-1)[0] }}
            <span class="tooltiptext">
              {{ data.device_class }}
            </span>
          </div>
        </template>
        <template #filter="{filterModel,filterCallback}">
          <Dropdown v-model="filterModel.value" :options="device_classes"
            placeholder="Any" class="p-column-filter" :showClear="true"
            @change="filterCallback()" >
          </Dropdown>
        </template>
      </Column>
      <Column field="prefix" header="Prefix" :sortable="true">
        <template #body="{data}">
          <router-link :to="`/whatrec/${data.prefix}*/${data.prefix}`">
            {{data.prefix}}
          </router-link>
        </template>
        <template #filter="{filterModel,filterCallback}">
          <InputText type="text" v-model="filterModel.value" @keydown.enter="filterCallback()" class="p-column-filter"
            :placeholder="`Filter by prefix`" />
        </template>
      </Column>
      <Column field="active" header="Active" :sortable="true">
        <template #body="{data}">
          <i :class="['pi', data.active ? 'pi-check' : 'pi-times']" />
        </template>
        <template #filter="{filterModel,filterCallback}">
          <Dropdown v-model="filterModel.value" :options="[false, true]"
            placeholder="Any" class="p-column-filter" :showClear="true"
            @change="filterCallback()" >
          </Dropdown>
        </template>
      </Column>
      <Column v-for="(col, index) of selected_columns"
        :field="col.field" :header="col.header"
        :key="col.field + '_' + index"
        :sortable="true"
        >
          <template #filter="{filterModel,filterCallback}">
            <InputText type="text" v-model="filterModel.value" @keydown.enter="filterCallback()" class="p-column-filter"
              :placeholder="`Filter`" />
          </template>
      </Column>
    </DataTable>
  </template>
</template>

<script>
import { mapState } from 'vuex';

import Button from 'primevue/button';
import Column from 'primevue/column';
import DataTable from 'primevue/datatable';
import Dropdown from 'primevue/dropdown';
import InputText from 'primevue/inputtext';
import MultiSelect from 'primevue/multiselect';
import {FilterMatchMode} from 'primevue/api';

import DictionaryTable from '../components/dictionary-table.vue'

export default {
  name: 'HappiView',
  components: {
    Button,
    Column,
    DataTable,
    DictionaryTable,
    Dropdown,
    InputText,
    MultiSelect,
  },
  props: {
    item_name: String,
  },
  data() {
    return {
      filters: null,
      selected_columns: null,
    }
  },
  computed: {
    happi_item_info () {
      if (!this.item_name || !this.happi_items || !this.happi_info_ready) {
        return {
          "error": "",
          "_whatrecord": {"records": []},
        };
      }
      return this.happi_info.metadata_by_key[this.item_name];
    },
    device_classes() {
      let classes = new Set();
      for (const happi_item of this.happi_items) {
        classes.add(happi_item.device_class);
      }
      return Array.from(classes).sort();
    },
    kind_to_related_records() {
      const info = this.happi_item_info;
      if (!info) {
        return {};
      }
      let result = {
        "Kind.hinted": [],
        "Kind.normal": [],
      };
      for (const rec of info._whatrecord.records) {
        if (rec.kind in result === false) {
          result[rec.kind] = [];
        }
        result[rec.kind].push(rec);
      }
      return result;
    },
    happi_items () {
      if (Object.keys(this.happi_info).length == 0) {
        return [];
      }
      return Object.values(this.happi_info.metadata_by_key);
    },

    global_filter_fields () {
      let fields = ["name", "device_class", "prefix"];
      for (const col of this.selected_columns) {
        fields.push(col.field);
      }
      return fields;
    },
    ...mapState({
      happi_info_ready (state) {
        return Object.keys(state.plugin_info).length > 0;
      },
      happi_info (state) {
        if (!state.plugin_info) {
          return {};
        }
        const happi_info = state.plugin_info.happi || {
            metadata_by_key: {},
          };
        return happi_info;
      },
    }),
  },
  created() {
    document.title = `WhatRecord? happi plugin`;
    this.init_filters();
  },
  mounted() {
    if (!this.happi_info_ready) {
        this.$store.dispatch("update_plugin_info");
    }
  },
  methods: {
    init_filters() {
      this.filters = {
        global: {value: "", matchMode: FilterMatchMode.CONTAINS},
        name: {value: "", matchMode: FilterMatchMode.CONTAINS},
        device_class: {value: "", matchMode: FilterMatchMode.CONTAINS},
        prefix: {value: "", matchMode: FilterMatchMode.CONTAINS},
        beamline: {value: "", matchMode: FilterMatchMode.CONTAINS},
        stand: {value: "", matchMode: FilterMatchMode.CONTAINS},
        active: {value: "", matchMode: FilterMatchMode.EQUALS},
        z: {value: "", matchMode: FilterMatchMode.CONTAINS},
        last_edit: {value: "", matchMode: FilterMatchMode.CONTAINS},
        args: {value: "", matchMode: FilterMatchMode.CONTAINS},
        kwargs: {value: "", matchMode: FilterMatchMode.CONTAINS},
      };
      this.columns = [
        {field: 'beamline', header: 'Beamline'},
        {field: 'stand', header: 'Stand'},
        {field: 'z', header: 'Z Location (m)'},
        {field: 'last_edit', header: 'Last Edit'},
        {field: 'args', header: 'Arguments'},
        {field: 'kwargs', header: 'Keyword Arguments'},
      ]
      this.selected_columns = this.columns.slice(0, 2);
    },
    clear_filters() {
      this.init_filters();
    },
    onToggle(value) {
      this.selected_columns = value;
    }
  },

}
</script>

<!-- https://www.w3schools.com/css/css_tooltip.asp -->
<style scoped>
.tooltip {
  position: relative;
  display: inline-block;
  border-bottom: 1px dashed black;
}

.tooltip .tooltiptext {
  visibility: hidden;
  width: auto;
  background-color: lightblue;
  color: black;
  text-align: center;
  padding: 5px 0;
  border-radius: 6px;
  position: absolute;
  z-index: 1;
}

.tooltip:hover .tooltiptext {
  visibility: visible;
}

#related_records {
  border-collapse: collapse;
}

#related_records td, #related_records th {
  border: 1px solid #ddd;
  padding: 8px;
}

#related_records tr:nth-child(even){
  background-color: var(--surface-b);
}

#related_records tr:hover {
  background-color: var(--surface-c);
}

#related_records th {
  padding-top: 12px;
  padding-bottom: 12px;
  text-align: center;
  border: 1px solid;
}

#related_records td {
  font-family: monospace;
}
</style>
