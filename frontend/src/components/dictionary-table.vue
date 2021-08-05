<template>
  <table :class="[cls, table_default_class, path_table_class]" v-if="Object.keys(filtered_dict).length > 0">
    <thead>
      <tr>
        <th>{{ key_column || "Key" }}</th>
        <th>{{ value_column || "Value" }}</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="value, key in filtered_dict" :key="key + '-' + value">
        <td class="key">{{ key }}</td>

        <td v-if="key == 'context'" class="value">
          <script-context-link :context=value :short=true></script-context-link>
        </td>
        <td v-else-if="key == 'metadata'" class="value">
          <dictionary-table
            :dict="value"
            cls="metadata"
            :path="path ? path + '.' + key : key"
            :skip_keys="[]" />
        </td>
        <td v-else-if="value instanceof Array" class="value">
          <ul>
            <li v-for="item of value" :key="item">
              <template v-if="item && Object.keys(item)[0] != 0">
                <dictionary-table
                  :dict="item"
                  cls="metadata"
                  :skip_keys="[]" />
              </template>
              <template v-else>
                {{ item }}
              </template>
            </li>
          </ul>
        </td>
        <td v-else-if="value instanceof Object" class="value">
          <dictionary-table
            :dict="value"
            cls=""
            :path="path ? path + '.' + key : key"
            :skip_keys="[]" />
        </td>
        <td v-else class="value">
          <pre>{{ value }}</pre>
        </td>
      </tr>
    </tbody>
  </table>
</template>

<script>
import ScriptContextLink from './script-context-link.vue';

export default {
  name: 'DictionaryTable',
  props: {
    cls: String,
    dict: Object,
    skip_keys: Array,
    key_column: String,
    value_column: String,
    path: String,
  },
  components: {
    ScriptContextLink,
  },
  computed: {
    nest_level() {
      const path = this.path || "";
      return (path.match(/\./g) || []).length;
    },
    path_table_class() {
      return this.path ? `path-${this.path}` : "";
    },
    table_default_class() {
      return `nested-${this.nest_level}`;
    },
    filtered_dict() {
      let filtered = {};
      if (this.dict === null || this.dict === undefined) {
          return filtered;
      }
      for (const [key, value] of Object.entries(this.dict)) {
        if (this.skip_keys.indexOf(key) < 0 && value != null) {
          const looks_worth_displaying = (
            typeof value == "boolean" ||
            typeof value == "number" ||
            Object.entries(value).length > 0
          );
          if (looks_worth_displaying) {
            filtered[key] = value;
          }
        }
      }
      return filtered;
    },
  }
}
</script>

<style scoped>
th, td {
  padding: 5px 5px;
}

table {
  font-size: 12px;
  font-weight: normal;
  border: thin solid;
  border-collapse: collapse;
  width: 100%;
  max-width: 100%;
  white-space: nowrap;
  background-color: var(--surface-f);
}

table .metadata {
  border: thin dotted;
}

table .nested-0 {
  border: thin dotted;
}

table .nested-1 {
  border: thin dashed;
}

th {
  text-align: center;
  padding: 8px;
}

td .key {
  text-align: right;
  padding: 8px;
  border-right: 1px solid #f8f8f8;
  font-size: 1em;
  font-weight: bold;
}

td .value {
  text-align: left;
  padding: 8px;
  border-right: 1px solid #f8f8f8;
  font-size: 1em;
}

thead th {
  color: var(--text-color);
  border: solid black 1px;
  font-size: 1.2em;
  background: var(--surface-d);
}

thead th:nth-child(odd) {
  color: var(--text-color);
}

tr:nth-child(even) {
  background: var(--surface-b);
}

ul {
    list-style: none;
    margin-left: 0;
    padding-left: 1em;
}
ul > li:before {
    display: inline-block;
    content: "-";
    width: 1em;
    margin-left: -1em;
}
pre {
  margin: 0px;
}
</style>
