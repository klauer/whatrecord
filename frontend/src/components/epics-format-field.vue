<template>
  <div :title="tooltip">
    &nbsp;field(<template v-if="field_info?.context?.length > 0">
      <script-context-one-link
        :name="field_info.context[0][0]"
        :line="field_info.context[0][1]"
        :link_text="field.name"
        class="unassuming_link"
      />
    </template>
    <template v-else> {{ field.name }} </template>,
    <template v-if="field?.context?.length > 0">
      <script-context-one-link
        :name="field.context[0][0]"
        :line="field.context[0][1]"
        :link_text="`&quot;${display_value}&quot;`"
        class="unassuming_link"
      />)
    </template>
    <template v-else> "{{ display_value }}") </template>
    <br />
  </div>
</template>

<script>
import ScriptContextOneLink from "./script-context-one-link.vue";

export default {
  name: "EpicsFormatField",
  props: {
    autosave: Object,
    field: Object,
    field_info: Object,
    menus: Object,
  },
  components: {
    ScriptContextOneLink,
  },
  computed: {
    field_info_text() {
      const info = this.field_info;
      if (!info || !Object.keys(info).length) {
        return "";
      }
      let field_body = [];
      const known_keys = [
        "asl",
        "initial",
        "promptgroup",
        "prompt",
        "special",
        "pp",
        "interest",
        "base",
        "size",
        "extra",
        "menu",
        "prop",
      ];
      for (const [key, value] of Object.entries(info)) {
        if (known_keys.indexOf(key) >= 0) {
          field_body.push(`${key}: "${value}"`);
        }
      }
      for (const [key, value] of Object.entries(info.body || {})) {
        field_body.push(`? ${key}: "${value}"`);
      }

      if (info.menu) {
        const menu_options = this.menu_options;

        if (menu_options === null) {
          field_body.push("? No menu definition");
        } else {
          field_body.push("");
          field_body.push("Menu options:");
          let idx = 0;
          for (const [key, value] of Object.entries(menu_options)) {
            field_body.push(`[Menu ${idx}] "${value}" ("${key}")`);
            idx += 1;
          }
        }
      }

      return field_body.join("\n");
    },

    display_value() {
      return this.menu_text ?? this.raw_value;
    },

    menu_options() {
      if (!this.field_info?.menu || !this.menus) {
        return null;
      }
      return this.menus[this.field_info.menu]?.choices;
    },

    raw_value() {
      return this.autosave?.value ?? this.field.value;
    },

    menu_text() {
      const menu_options = this.menu_options;
      if (!menu_options) {
        return null;
      }
      const field_value = parseInt(this.raw_value);
      if (isNaN(field_value)) {
        return null;
      }
      return Object.values(menu_options)[field_value];
    },

    tooltip() {
      const ctx = this.field.context?.join(":");
      if (ctx === null) {
        return "";
      }
      return `
Data type: ${this.field.dtype}
Context: ${ctx}
-
Database value: ${this.field.value}
Autosave value: ${this.autosave?.value}
-
${this.field_info_text}
`.trim();
    },
  },
};
</script>

<style scoped>
.unassuming_link {
  color: var(--text-color);
  text-decoration: none;
  font-weight: normal;
}
</style>
