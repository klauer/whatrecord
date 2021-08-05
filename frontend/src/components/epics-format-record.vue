<template>
  <template v-if="!is_pva">
    <div class="code">
      <template v-if="is_grecord">g</template>
      record({{ record_type }}, "{{ name }}") {<br/>
      <!-- aliases -->
      <span v-for="alias in aliases" :key="alias">
        &nbsp;alias("{{ alias }}")<br/>
      </span>
      <!-- fields -->
      <template v-for="field in fields" :key="field.name">
        <epics-format-field :field="field"
            :field_info="record_defn ? record_defn.fields[field.name] : null"
            :menus="menus"
            />
      </template>
      <!-- info nodes -->
      <span v-for="[key, value] in info_nodes" :key="key">
        &nbsp;info({{ key }}, "{{ value }}")<br/>
      </span>
    }
    </div>
  </template>
  <template v-else>
    <div class="code">
      "{{ name }}":<br/>
      <span
        class="recordfield"
        v-for="field in fields"
        :key="field.name"
        :title="field.dtype + ':' + field.context.join(', ')"
        >
      &nbsp;
      <script-context-one-link
        :name="field.context[0][0]"
        :line="field.context[0][1]"
        :link_text="field.name"
        class="unassuming_link"
        /> from
      from "{{ field.record_name }}.{{ field.field_name }}"
      <template v-if="Object.keys(field.metadata).length > 0">#  {{ field.metadata }}</template>
      <br/>
      </span>
    </div>
  </template>
</template>

<script>
import EpicsFormatField from './epics-format-field.vue'
import ScriptContextOneLink from './script-context-one-link.vue'

export default {
  name: 'EpicsFormatRecord',
  props: {
    context: Array,
    aliases: Array,
    fields: Object,
    is_grecord: Boolean,
    is_pva: Boolean,
    metadata: Object,
    name: String,
    record_type: String,
    record_type_info: Object,
    record_defn: Object,
    menus: Object,
  },
  components: {
    EpicsFormatField,
    ScriptContextOneLink,
  },
  computed: {
    info_nodes() {
      const skip_keys = ["asg", "gateway", "archived", "happi", "streamdevice"];
      return Object.entries(this.metadata).filter(
        item => skip_keys.indexOf(item[0]) < 0
      );
    },
  },
}
</script>

<style scoped>
.unassuming_link {
  color: black;
  text-decoration:none;
}
</style>
