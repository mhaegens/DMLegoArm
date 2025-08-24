# Pod Plugin

This directory provides a minimal client plugin for the DMLegoArm server. It contains the static assets required to render the plugin UI and configure how the host loads it.

## Contents

- `manifest.json` – describes the plugin and points to the entry file.
- `index.html` – simple web page served when the plugin is loaded.
- `script.js` – JavaScript executed on load.
- `styles.css` – basic styling for the page.
- `plugin.properties` – configuration flags used by the server.

## Deploy

No build step is required. Copy all files in this directory to your server's plugin folder. The server reads `manifest.json` to register the plugin and serves `index.html` to provide the UI.
