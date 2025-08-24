sap.ui.define([
  "sap/dm/dme/podfoundation/controller/PluginViewController",
  "sap/ui/model/json/JSONModel",
  "sap/m/MessageToast",
  "sap/m/Dialog",
  "sap/m/Label",
  "sap/m/Input",
  "sap/m/Select",
  "sap/ui/core/Item",
  "sap/m/CheckBox",
  "sap/m/Button",
  "sap/m/TextArea"
], function (PluginViewController, JSONModel, MessageToast, Dialog, Label, Input, Select, Item, CheckBox, Button, TextArea) {
  "use strict";

  var STORAGE_KEY = "lego.arm.cfg";

  function nowIso() {
    return new Date().toISOString();
  }

  return PluginViewController.extend("lego.haegens.plugins.legoarm.controller.MainView", {
    onInit: function () {
      PluginViewController.prototype.onInit.apply(this, arguments);
      this._cfg = this._loadConfig();
      this._bindPodConfigBridge();
      this._setStatus("idle");
      this._log("Plugin loaded", this._cfg);
    },

    /* ======================
       Config helpers
       ====================== */
    _loadConfig: function () {
      var defaults = {
        baseUrl: "http://legopi.local:5000",
        authMode: "none",     // none | basic | bearer | x-api-key
        username: "",
        password: "",
        bearerToken: "",
        apiKey: ""
      };

      // 1) localStorage
      var ls = {};
      try { ls = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}"); } catch (e) {}

      // 2) component data (when passed by POD designer)
      var compData = (this.getOwnerComponent && this.getOwnerComponent().getComponentData && this.getOwnerComponent().getComponentData()) || {};
      var compCfg = compData.config || compData.properties || {};

      // 3) window bridge (set in index.html when the POD posts a message)
      var bridge = (window && window.__POD_CONFIG__) || {};

      // priority: defaults <- localStorage <- component <- bridge
      var merged = Object.assign({}, defaults, ls, compCfg, bridge);
      return merged;
    },

    _saveConfig: function (cfg) {
      this._cfg = Object.assign({}, this._cfg, cfg || {});
      try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(this._cfg)); } catch (e) {}
      this._log("Config saved", this._cfg);
    },

    _bindPodConfigBridge: function () {
      var that = this;
      document.addEventListener("POD_CONFIG_UPDATED", function (evt) {
        var payload = (evt && evt.detail) || {};
        that._saveConfig(payload);
        MessageToast.show("POD config received");
      });
    },

    /* ======================
       UI helpers
       ====================== */
    _setStatus: function (txt, type) {
      var strip = this.byId("statusStrip");
      if (strip) {
        strip.setText("Status: " + txt);
        strip.setType(type || "Information");
      }
    },

    _busy: function (flag) {
      var b = this.byId("busy");
      if (b) b.setVisible(!!flag);
    },
_log: function (msg, obj) {
  var ta = this.byId("console");
  if (!ta) return;

  var line = "[" + nowIso() + "] " + msg;
  if (obj !== undefined) {
    try { line += "\n" + JSON.stringify(obj, null, 2); }
    catch (e) { line += "\n" + String(obj); }
  }

  var current = ta.getValue() || "";
  ta.setValue((current ? current + "\n" : "") + line + "\n");

  // Move caret to end and scroll the textarea
  try {
    var len = ta.getValue().length;
    if (typeof ta.selectText === "function") {
      ta.selectText(len, len);
    } else {
      sap.ui.getCore().applyChanges();
      var dom = ta.getFocusDomRef && ta.getFocusDomRef();
      if (dom) {
        if (dom.setSelectionRange) dom.setSelectionRange(len, len);
        dom.scrollTop = dom.scrollHeight;
      }
    }
  } catch (e) {
    // non-fatal
  }
},

    onClearConsole: function () {
      var ta = this.byId("console");
      if (ta) ta.setValue("");
    },

    /* ======================
       Networking
       ====================== */
    _url: function (path) {
      var base = (this._cfg.baseUrl || "").replace(/\/$/, "");
      return base + path;
    },

    _authHeaders: function () {
      var h = {
        "Content-Type": "application/json"
      };
      // Helpful when calling ngrok
      h["ngrok-skip-browser-warning"] = "true";

      if (this._cfg.authMode === "basic" && this._cfg.username) {
        var token = btoa((this._cfg.username || "") + ":" + (this._cfg.password || ""));
        h["Authorization"] = "Basic " + token;
      } else if (this._cfg.authMode === "bearer" && this._cfg.bearerToken) {
        h["Authorization"] = "Bearer " + this._cfg.bearerToken;
      } else if (this._cfg.authMode === "x-api-key" && this._cfg.apiKey) {
        h["X-API-Key"] = this._cfg.apiKey;
      }
      return h;
    },

    _fetchJson: async function (path, options) {
      var url = this._url(path);
      var init = Object.assign({ headers: this._authHeaders() }, options || {});
      this._log("HTTP " + (init.method || "GET") + " " + url);

      var res, text, body;
      try {
        res = await fetch(url, init);
        text = await res.text();
        try { body = JSON.parse(text); } catch (e) { body = text; }
      } catch (netErr) {
        this._log("Network error", String(netErr));
        throw netErr;
      }

      if (!res.ok) {
        var err = new Error("HTTP " + res.status + " " + res.statusText);
        err.status = res.status;
        err.body = body;
        this._log("Error response", { status: res.status, body: body });
        if (res.status === 401) {
          this._log("Tip", "401 usually means missing or invalid credentials. Check Settings and your API auth.");
        }
        throw err;
      }
      return body;
    },

    /* ======================
       Settings dialog
       ====================== */
    onOpenSettings: function () {
      var that = this;
      if (this._dlg) {
        this._dlg.open();
        return;
      }

      var cfg = Object.assign({}, this._cfg);

      var inpBase = new Input({ width: "100%", value: cfg.baseUrl, placeholder: "http://legopi.local:5000" });
      var selAuth = new Select({
        width: "100%",
        selectedKey: cfg.authMode || "none",
        items: [
          new Item({ key: "none", text: "None" }),
          new Item({ key: "basic", text: "Basic" }),
          new Item({ key: "bearer", text: "Bearer" }),
          new Item({ key: "x-api-key", text: "X-API-Key" })
        ]
      });
      var inpUser = new Input({ width: "100%", value: cfg.username });
      var inpPass = new Input({ width: "100%", value: cfg.password, type: "Password" });
      var inpBearer = new Input({ width: "100%", value: cfg.bearerToken });
      var inpApiKey = new Input({ width: "100%", value: cfg.apiKey });

      function toggleAuthFields(mode) {
        inpUser.setVisible(mode === "basic");
        inpPass.setVisible(mode === "basic");
        inpBearer.setVisible(mode === "bearer");
        inpApiKey.setVisible(mode === "x-api-key");
      }
      selAuth.attachChange(function (e) { toggleAuthFields(e.getParameter("selectedItem").getKey()); });
      toggleAuthFields(cfg.authMode || "none");

      this._dlg = new Dialog({
        title: "Settings",
        contentWidth: "28rem",
        content: [
          new Label({ text: "Base URL" }), inpBase,
          new Label({ text: "Auth mode" }), selAuth,
          new Label({ text: "Username" }), inpUser,
          new Label({ text: "Password" }), inpPass,
          new Label({ text: "Bearer token" }), inpBearer,
          new Label({ text: "X-API-Key" }), inpApiKey
        ],
        beginButton: new Button({
          text: "Save",
          type: "Emphasized",
          press: function () {
            var newCfg = {
              baseUrl: inpBase.getValue().trim().replace(/\/$/, ""),
              authMode: selAuth.getSelectedKey(),
              username: inpUser.getValue(),
              password: inpPass.getValue(),
              bearerToken: inpBearer.getValue(),
              apiKey: inpApiKey.getValue()
            };
            that._saveConfig(newCfg);
            that._dlg.close();
            MessageToast.show("Settings saved");
          }
        }),
        endButton: new Button({ text: "Close", press: function () { that._dlg.close(); } }),
        afterClose: function () {}
      });
      this.getView().addDependent(this._dlg);
      this._dlg.open();
    },

    /* ======================
       Quick actions
       ====================== */
    onHealth: async function () {
      this._busy(true); this._setStatus("checking health");
      try {
        var data = await this._fetchJson("/health");
        this._log("Health", data);
        this._setStatus("health ok", "Success");
      } catch (e) {
        this._setStatus("health error - " + e.message, "Error");
      } finally { this._busy(false); }
    },

    onInventory: async function () {
      this._busy(true); this._setStatus("getting inventory");
      try {
        var data = await this._fetchJson("/inventory");
        this._log("Inventory", data);
        this._setStatus("inventory ok", "Success");
      } catch (e) {
        this._setStatus("inventory error - " + e.message, "Error");
      } finally { this._busy(false); }
    },

    onReboot: async function () {
      this._busy(true); this._setStatus("sending reboot");
      try {
        var data = await this._fetchJson("/reboot", { method: "POST" });
        this._log("Reboot", data);
        this._setStatus("reboot accepted", "Success");
      } catch (e) {
        this._setStatus("reboot error - " + e.message, "Error");
      } finally { this._busy(false); }
    },

    onPick: async function () {
      this._busy(true); this._setStatus("sending pick");
      try {
        // Choose the endpoint you exposed. Example uses query param.
        var data = await this._fetchJson("/move?command=pick");
        this._log("Pick", data);
        this._setStatus("pick ok", "Success");
      } catch (e) {
        this._setStatus("pick error - " + e.message, "Error");
      } finally { this._busy(false); }
    },

    onPlace: async function () {
      this._busy(true); this._setStatus("sending place");
      try {
        var data = await this._fetchJson("/move?command=place");
        this._log("Place", data);
        this._setStatus("place ok", "Success");
      } catch (e) {
        this._setStatus("place error - " + e.message, "Error");
      } finally { this._busy(false); }
    },

    onPose: async function () {
        this._busy(true); this._setStatus("sending pose");
        try {
            var payload = this._buildMovePayload("pose"); // reuses your input fields
            // If you want pose to always be zeros, uncomment next line:
            // payload.joints = payload.motors = { A:0, B:0, C:0, D:0 };
            var data = await this._fetchJson("/move", {
            method: "POST",
            body: JSON.stringify(payload)
            });
            this._log("Pose", data);
            this._setStatus("pose ok", "Success");
        } catch (e) {
            this._setStatus("pose error - " + e.message, "Error");
        } finally { this._busy(false); }
    },


    onPose: async function () {
      this._busy(true); this._setStatus("sending pose");
      try {
        var body = { command: "pose", joints: { A: 0, B: 0, C: 0, D: 0 }, speed: 40 };
        var data = await this._fetchJson("/move", { method: "POST", body: JSON.stringify(body) });
        this._log("Pose", data);
        this._setStatus("pose ok", "Success");
      } catch (e) {
        this._setStatus("pose error - " + e.message, "Error");
      } finally { this._busy(false); }
    },

    /* ======================
       Move flow
       ====================== */
    _buildMovePayload: function (command) {
    var num = function (id, def) {
        var c = this.byId(id);
        var v = c ? Number(c.getValue()) : def;
        return isFinite(v) ? v : def;
    }.bind(this);

    var joints = {
        A: num("degA", 0),
        B: num("degB", 0),
        C: num("degC", 0),
        D: num("degD", 0)
    };

    var payload = {
        command: command || "move",
        joints: joints,
        // compatibility aliases in case the backend looks for "motors" or "acceleration"
        motors: joints,
        speed: num("speed", 180),
        accel: num("accel", 360),
        acceleration: num("accel", 360),
        dryRun: !!(this.byId("dryRun") && this.byId("dryRun").getSelected && this.byId("dryRun").getSelected())
    };

    return payload;
    },


    onPreviewMove: function () {
    var ta = this.byId("movePreview");
    if (!ta) return;
    var payload = this._buildMovePayload("move");
    ta.setValue(JSON.stringify(payload, null, 2));
    this._log("Move preview", payload);
    },


    onSendMove: async function () {
    this._busy(true); this._setStatus("sending move");
    try {
        var payload = this._buildMovePayload("move");
        var data = await this._fetchJson("/move", { method: "POST", body: JSON.stringify(payload) });
        this._log("Move response", data);
        this._setStatus("move ok", "Success");
    } catch (e) {
        this._setStatus("move error - " + e.message, "Error");
    } finally { this._busy(false); }
    },

  });
});
