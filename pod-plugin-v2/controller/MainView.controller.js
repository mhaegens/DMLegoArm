sap.ui.define([
  "sap/dm/dme/podfoundation/controller/PluginViewController",
  "sap/ui/model/json/JSONModel",
  "sap/m/MessageToast",
  "sap/m/MessageBox",
  "sap/m/Dialog",
  "sap/m/Label",
  "sap/m/Input",
  "sap/m/Select",
  "sap/ui/core/Item",
  "sap/m/Button",
  "sap/m/TextArea"
], function (PluginViewController, JSONModel, MessageToast, MessageBox, Dialog, Label, Input, Select, Item, Button, TextArea) {
  "use strict";

  var STORAGE_KEY = "lego.arm.cfg.v2";

  function nowIso() {
    return new Date().toISOString();
  }

  return PluginViewController.extend("sap.michielh.legoarm.controller.MainView", {
    onInit: function () {
      PluginViewController.prototype.onInit.apply(this, arguments);
      this._cfg = this._loadConfig();
      this._bindPodConfigBridge();

      var vm = new JSONModel({
        statusText: "Status: idle",
        statusType: "Information",
        health: { text: "Unknown", state: "None", updated: "" },
        presetSpeed: 80,
        nudgeAmount: 10,
        nudgeSpeed: 40,
        positions: [],
        poseSummary: "No data yet",
        busy: false,
        points: {},
        processes: []
      });
      this.getView().setModel(vm, "view");

      this._log("Plugin v2 loaded", this._cfg);
      this.onRefreshState();
      this.onHealth();
      this.onRefreshProcesses();
    },

    /* ======================
       Config helpers
       ====================== */
    _loadConfig: function () {
      var defaults = {
        baseUrl: "http://legopi.local:8000",
        authMode: "none",     // none | basic | bearer | x-api-key
        username: "",
        password: "",
        bearerToken: "",
        apiKey: ""
      };

      var ls = {};
      try { ls = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}"); } catch (e) {}

      var compData = (this.getOwnerComponent && this.getOwnerComponent().getComponentData && this.getOwnerComponent().getComponentData()) || {};
      var compCfg = compData.config || compData.properties || {};

      var bridge = (window && window.__POD_CONFIG__) || {};

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
      var vm = this.getView().getModel("view");
      vm.setProperty("/statusText", "Status: " + txt);
      vm.setProperty("/statusType", type || "Information");
    },

    _setBusy: function (flag) {
      var vm = this.getView().getModel("view");
      vm.setProperty("/busy", !!flag);
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
      } catch (e) {}
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

      var inpBase = new Input({ width: "100%", value: cfg.baseUrl, placeholder: "http://legopi.local:8000" });
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
       Data fetchers
       ====================== */
    onHealth: async function () {
      this._setBusy(true); this._setStatus("checking health");
      try {
        var data = await this._fetchJson("/v1/health");
        var ok = data && data.ok !== false;
        var vm = this.getView().getModel("view");
        vm.setProperty("/health", {
          text: ok ? "Healthy" : "Needs attention",
          state: ok ? "Success" : "Error",
          updated: nowIso()
        });
        this._log("Health", data);
        this._setStatus("health ok", ok ? "Success" : "Warning");
      } catch (e) {
        this._setStatus("health error - " + e.message, "Error");
      } finally { this._setBusy(false); }
    },

    onRefreshState: async function () {
      this._setBusy(true); this._setStatus("refreshing state");
      try {
        var state = await this._fetchJson("/v1/arm/state");
        this._log("Arm state", state);
        this._updateStateModel(state);
        this._setStatus("state ready", "Success");
      } catch (e) {
        this._setStatus("state error - " + e.message, "Error");
      } finally { this._setBusy(false); }
    },

    onShutdown: function () {
      var that = this;
      MessageBox.warning(
        "Move to the shutdown pose (A open, B/C min, D neutral) and power off the Pi?",
        {
          title: "Confirm shutdown",
          actions: [MessageBox.Action.CANCEL, MessageBox.Action.OK],
          emphasizedAction: MessageBox.Action.OK,
          onClose: async function (action) {
            if (action !== MessageBox.Action.OK) { return; }
            that._setBusy(true); that._setStatus("starting shutdown", "Warning");
            try {
              var res = await that._fetchJson("/v1/processes/shutdown", { method: "POST", body: JSON.stringify({}) });
              that._log("Shutdown sequence", res);
              MessageToast.show("Shutdown sequence started");
              that._setStatus("shutdown scheduled", "Success");
            } catch (e) {
              that._setStatus("shutdown error - " + e.message, "Error");
              MessageToast.show("Shutdown failed: " + e.message);
            } finally { that._setBusy(false); }
          }
        }
      );
    },

    onRunProcess: async function (oEvent) {
      var name = oEvent && oEvent.getSource && oEvent.getSource().data("name");
      if (!name) { return; }

      this._setBusy(true); this._setStatus("starting process '" + name + "'");
      try {
        var res = await this._fetchJson("/v1/processes/" + name, { method: "POST", body: JSON.stringify({}) });
        this._log("Process started", res);
        MessageToast.show("Process '" + name + "' started");
        this._setStatus("process started", "Success");
      } catch (e) {
        this._setStatus("process error - " + e.message, "Error");
        MessageToast.show("Failed to start process '" + name + "': " + e.message);
      } finally {
        this._setBusy(false);
      }
    },

    _updateStateModel: function (payload) {
      var data = payload && payload.data ? payload.data : payload || {};
      var abs = data.abs_degrees || {};
      var limits = data.limits || {};
      var points = data.points || {};

      var rows = Object.keys(abs).map(function (j) {
        var lim = limits[j] || [];
        var low = lim[0];
        var high = lim[1];
        return {
          joint: j,
          label: this._jointLabel(j),
          deg: abs[j] + "°",
          limitText: (low !== undefined ? low : "?") + " … " + (high !== undefined ? high : "?")
        };
      }.bind(this));

      var summary = Object.keys(abs).map(function (j) { return j + ": " + abs[j] + "°"; }).join("  ");
      if (!summary) summary = "No position";

      var vm = this.getView().getModel("view");
      vm.setProperty("/positions", rows);
      vm.setProperty("/poseSummary", summary);
      vm.setProperty("/points", points);
    },

    _jointLabel: function (joint) {
      var names = { A: "Gripper", B: "Wrist", C: "Elbow", D: "Rotation" };
      return names[joint] || joint;
    },

    _getPoint: function (joint, name) {
      var vm = this.getView().getModel("view");
      var pts = vm.getProperty("/points") || {};
      var m = pts[joint] || {};
      return m[name];
    },

    /* ======================
       Movement helpers
       ====================== */
    _sendMove: async function (payload, statusLabel) {
      this._setBusy(true); this._setStatus(statusLabel || "sending move");
      try {
        var data = await this._fetchJson("/v1/arm/move", { method: "POST", body: JSON.stringify(payload) });
        this._log(statusLabel || "Move", data);
        this._setStatus("move ok", "Success");
      } catch (e) {
        this._setStatus("move error - " + e.message, "Error");
      } finally { this._setBusy(false); }
    },

    _moveToPoint: function (joint, pointName) {
      var value = this._getPoint(joint, pointName);
      if (value === undefined) {
        MessageToast.show("Point '" + pointName + "' not available for joint " + joint);
        return;
      }
      var vm = this.getView().getModel("view");
      var speed = Number(vm.getProperty("/presetSpeed")) || 60;
      var payload = { mode: "absolute", units: "degrees", joints: {}, speed: speed, finalize: true };
      payload.joints[joint] = value;
      this._sendMove(payload, "Move " + joint + " -> " + pointName);
    },

    _poseFromPoints: function (map) {
      var vm = this.getView().getModel("view");
      var speed = Number(vm.getProperty("/presetSpeed")) || 60;
      var joints = {};
      var missing = [];
      Object.keys(map).forEach(function (joint) {
        var point = map[joint];
        var val = this._getPoint(joint, point);
        if (val === undefined) {
          missing.push(joint + "::" + point);
        } else {
          joints[joint] = val;
        }
      }.bind(this));

      if (missing.length) {
        MessageToast.show("Missing calibration for: " + missing.join(", "));
        return;
      }

      var payload = { mode: "absolute", units: "degrees", joints: joints, speed: speed, finalize: true };
      this._sendMove(payload, "Pose preset");
    },

    _nudge: function (joint, dir) {
      var vm = this.getView().getModel("view");
      var amt = Number(vm.getProperty("/nudgeAmount")) || 1;
      var speed = Number(vm.getProperty("/nudgeSpeed")) || 40;
      var delta = dir === "plus" ? amt : -amt;
      var payload = {
        mode: "relative",
        units: "rotations",
        joints: {},
        speed: speed,
        finalize: true
      };
      payload.joints[joint] = delta;
      this._sendMove(payload, "Nudge " + joint + " " + delta + " rotations");
    },

    onCopyLimits: function () {
      var vm = this.getView().getModel("view");
      var limits = vm.getProperty("/positions") || [];
      var txt = limits.map(function (row) { return row.joint + ": " + row.limitText; }).join("\n");
      if (navigator && navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt).then(function () {
          MessageToast.show("Limits copied");
        }).catch(function () {
          MessageToast.show("Copy failed");
        });
      } else {
        this._log("Limits", txt);
        MessageToast.show("Clipboard API not available; logged instead");
      }
    },

    onRefreshProcesses: async function () {
      this._setBusy(true); this._setStatus("loading processes");
      try {
        var inv = await this._fetchJson("/v1/inventory");
        var list = (inv && inv.processes) || [];
        this.getView().getModel("view").setProperty("/processes", list);
        this._log("Processes", list);
        this._setStatus("processes loaded", "Success");
      } catch (e) {
        this._setStatus("processes error - " + e.message, "Error");
        MessageToast.show("Failed to load processes: " + e.message);
      } finally {
        this._setBusy(false);
      }
    },

    /* ======================
       Joint preset handlers
       ====================== */
    onJointPresetAOpen: function () { this._moveToPoint("A", "open"); },
    onJointPresetAClose: function () { this._moveToPoint("A", "closed"); },
    onJointPresetBMin: function () { this._moveToPoint("B", "min"); },
    onJointPresetBPick: function () { this._moveToPoint("B", "pick"); },
    onJointPresetBMax: function () { this._moveToPoint("B", "max"); },
    onJointPresetCMin: function () { this._moveToPoint("C", "min"); },
    onJointPresetCPick: function () { this._moveToPoint("C", "pick"); },
    onJointPresetCMax: function () { this._moveToPoint("C", "max"); },
    onJointPresetDAssembly: function () { this._moveToPoint("D", "assembly"); },
    onJointPresetDQuality: function () { this._moveToPoint("D", "quality"); },
    onJointPresetDNeutral: function () { this._moveToPoint("D", "neutral"); },

    /* ======================
       Pose shortcuts
       ====================== */
    onPosePickAssembly: function () {
      this._poseFromPoints({ A: "open", B: "pick", C: "pick", D: "assembly" });
    },
    onPosePickQuality: function () {
      this._poseFromPoints({ A: "open", B: "pick", C: "pick", D: "quality" });
    },
    onPoseNeutral: function () {
      this._poseFromPoints({ A: "open", B: "pick", C: "pick", D: "neutral" });
    },
    onPoseFlex: function () {
      this._poseFromPoints({ A: "open", B: "max", C: "max", D: "neutral" });
    },

    /* ======================
       Nudge handlers
       ====================== */
    onNudgeAPlus: function () { this._nudge("A", "plus"); },
    onNudgeAMinus: function () { this._nudge("A", "minus"); },
    onNudgeBPlus: function () { this._nudge("B", "plus"); },
    onNudgeBMinus: function () { this._nudge("B", "minus"); },
    onNudgeCPlus: function () { this._nudge("C", "plus"); },
    onNudgeCMinus: function () { this._nudge("C", "minus"); },
    onNudgeDPlus: function () { this._nudge("D", "plus"); },
    onNudgeDMinus: function () { this._nudge("D", "minus"); }
  });
});
