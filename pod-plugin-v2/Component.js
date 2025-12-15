sap.ui.define([
  "sap/dm/dme/podfoundation/component/production/ProductionUIComponent",
  "sap/ui/model/json/JSONModel"
], function (ProductionUIComponent, JSONModel) {
  "use strict";

  return ProductionUIComponent.extend("lego.haegens.plugins.legoarmv2.Component", {
    metadata: { manifest: "json" },

    init: function () {
      ProductionUIComponent.prototype.init.apply(this, arguments);

      // Read properties coming from POD Designer
      var compData = (this.getComponentData && this.getComponentData()) || {};
      var props    = compData.properties || compData.config || {};

      // Expose properties on a named model (optional, handy for bindings)
      this.setModel(new JSONModel({ properties: props }), "pod");

      // Bridge to window for simple consumption by controllers or plain JS
      // Merge with any pre-existing bridged config (e.g., posted via postMessage)
      try {
        var bridge = (window.__POD_CONFIG__) || {};
        window.__POD_CONFIG__ = Object.assign({}, bridge, props);
        document.dispatchEvent(new CustomEvent("POD_CONFIG_UPDATED", {
          detail: window.__POD_CONFIG__
        }));
      } catch (e) {
        // non-fatal
      }
    },

    /** Convenience accessor if you prefer calling from controllers */
    getPodProperties: function () {
      var m = this.getModel("pod");
      return m ? (m.getProperty("/properties") || {}) : {};
    }
  });
});
