# Pod Plugin

This directory contains the client plugin assets for the DMLegoArm server.

## Build

1. Install dependencies: `npm install`
2. Build the plugin bundle: `npm run build`

## Deploy

Copy the resulting files along with `manifest.json`, `index.html`, JavaScript, CSS, and `.properties` files to the server's plugin directory.

## Integration

The main server loads this plugin using `manifest.json` and serves `index.html` to provide the plugin UI.
