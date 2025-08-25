sap.ui.define([
  "sap/ui/model/resource/ResourceModel",
  "sap/dm/dme/podfoundation/control/PropertyEditor"
], function (ResourceModel, PropertyEditor) {
  "use strict";

  return PropertyEditor.extend("lego.haegens.plugins.legoarm.builder.PropertyEditor", {

    constructor: function (sId, mSettings) {
      PropertyEditor.apply(this, arguments);

      // i18n setup (kept as-is from your original)
      this.setI18nKeyPrefix("customComponentListConfig.");
      this.setResourceBundleName("lego.haegens.plugins.legoarm.i18n.builder");
      this.setPluginResourceBundleName("lego.haegens.plugins.legoarm.i18n.i18n");
    },

    /**
     * Build the Designer UI. Keep your original fields and add the API config.
     */
    addPropertyEditorContent: function (oPropertyFormContainer) {
      var oData = this.getPropertyData();

      // Original controls
      this.addSwitch(oPropertyFormContainer, "backButtonVisible", oData);
      this.addSwitch(oPropertyFormContainer, "closeButtonVisible", oData);
      this.addInputField(oPropertyFormContainer, "title", oData);
      this.addInputField(oPropertyFormContainer, "text", oData);

      // New: External API configuration
      // Base URL for the LEGO Arm API (e.g., http://legopi.local:8000)
      this.addInputField(oPropertyFormContainer, "baseUrl", oData);

      // Auth mode (string). Use: none | basic | bearer | x-api-key
      this.addInputField(oPropertyFormContainer, "authMode", oData);

      // Credentials (only the relevant ones will be used at runtime)
      this.addInputField(oPropertyFormContainer, "username", oData);
      this.addInputField(oPropertyFormContainer, "password", oData);      // keep as plain input; controller treats it as secret
      this.addInputField(oPropertyFormContainer, "bearerToken", oData);
      this.addInputField(oPropertyFormContainer, "apiKey", oData);
    },

    /**
     * Defaults shown when the plugin is first added in the POD Designer.
     */
    getDefaultPropertyData: function () {
      return {
        // existing
        "backButtonVisible": true,
        "closeButtonVisible": true,
        "title": "legoarm",
        "text": "legoarm",

        // new
        "baseUrl": "http://legopi.local:8000",
        "authMode": "none",       // none | basic | bearer | x-api-key
        "username": "",
        "password": "",
        "bearerToken": "",
        "apiKey": ""
      };
    }
  });
});
