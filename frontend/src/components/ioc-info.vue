<template>
  <h3>{{ ioc_info.name }}</h3>
  <dictionary-table
    :dict="ioc_info"
    :cls="'metadata'"
    :skip_keys="['commands', 'variables', 'loaded_files']">
  </dictionary-table>

  <template v-if="startup_file_list.length">
    <h3>Database files and scripts</h3>
    <DataTable :value="startup_file_list" dataKey="name">
      <Column field="name" header="File name">
        <template #body="{data}">
          <router-link :to="{ name: 'file', params: { filename: data.name, line: 0 } }">
            {{data.name}}
          </router-link>
        </template>
      </Column>
      <Column field="hash" header="Hash">
      </Column>
    </DataTable>
  </template>

  <details v-if="source_file_list.length">
    <summary>Source code files</summary>
    <DataTable :value="source_file_list" dataKey="name">
      <Column field="name" header="File name">
        <template #body="{data}">
          <router-link :to="{ name: 'file', params: { filename: data.name, line: 0 } }">
            {{data.name}}
          </router-link>
        </template>
      </Column>
      <Column field="hash" header="Hash">
      </Column>
      </DataTable>
  </details>
  <br />
</template>

<script>
import Column from 'primevue/column';
import DataTable from 'primevue/datatable';

import DictionaryTable from './dictionary-table.vue'

const startup_extensions = ["cmd", "db", "dbd"];

function get_extension(filename) {
  return filename.substring(filename.lastIndexOf('.') + 1, filename.length) || filename;
}

function is_startup_file(filename) {
  return startup_extensions.indexOf(get_extension(filename).toLowerCase()) >= 0;
}

export default {
  name: 'IocInfo',
  components: {
    Column,
    DataTable,
    DictionaryTable,
  },
  props: ["ioc_info"],
  data() {
    return {
    }
  },
  computed: {
    all_files () {
      let files = [];
      if (!this.ioc_info) {
        return files;
      }
      for (const [file, hash] of Object.entries(this.ioc_info.loaded_files)) {
        files.push({
          name: file,
          hash: hash,
        });
      }
      return files;
    },
    startup_file_list () {
      return this.all_files.filter(
        ({ name }) => is_startup_file(name)
      );
    },

    source_file_list () {
      return this.all_files.filter(
        ({ name }) => !is_startup_file(name)
      );
    },

  },

}
</script>

<style scoped>

</style>
