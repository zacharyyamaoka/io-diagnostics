// The placeholder workspaces for the layers RobotDx has decided on and not built yet: Deploy (software, OTA and config), Teleop and
// Data (storage). docs/peps/0002-robotdx-robot-health-super-app.md is the decision. Each is a calm page where the entity page sits,
// the device tree kept on the left (app.js: view=deploy | teleop | data, a page of the browser's history like 3D): the title and one
// line of purpose, what the layer will do, what it builds on, and a "Today" line that says it is not built yet.
// WHY text and links only: Zach, 2026-10-29: "no dead controls". A control that does nothing, or a number nobody measured, would say
// the layer exists. Every link here opens a page that does exist (the Devices tree, a device's own tabs, Activity).
'use strict';
(function () {
  const ICONS = { deploy: 'i-deploy', teleop: 'i-teleop', data: 'i-data' };
  const LAYERS = {
    deploy: {
      title: 'Software deploy, OTA and config',
      purpose: 'Know exactly what firmware, software, configuration and neural network each device runs, and change it safely.',
      will: [
        '<b>Versioned together.</b> Firmware, software, configs and neural networks are versioned as one set: a config is often pinned to a software version, and may be tuned.',
        '<b>Fully tracked.</b> What was installed where, and when, traceable for every device.',
        '<b>One device or a batch.</b> You act on one device, or batch by facility, state or fleet.',
        '<b>Live neural-network updates</b> go through the same path as firmware.',
      ],
    },
    teleop: {
      title: 'Teleop',
      purpose: 'Take over a robot that is stuck and get it moving again.',
      will: [
        '<b>A live camera</b> from the robot, over WebRTC.',
        '<b>Dragging the end effector</b> in Cartesian space, from the keyboard or the mouse.',
        '<b>Input devices</b> streamed to the robot, such as a Leap Motion.',
      ],
    },
    data: {
      title: 'Data storage',
      purpose: 'Know where a robot\'s recorded data lives, how long it is kept and what keeping it costs.',
      will: [
        '<b>Where recorded data is kept</b>, for each device and each robot.',
        '<b>Retention:</b> how long each kind of data stays before it is dropped.',
        '<b>Cost:</b> what storing it costs, so retention is a choice you can see.',
      ],
    },
  };
  const L = { api: null, key: '' };

  // a link to a page that exists: its href is the page's own URL (a new tab works), a plain click goes through the page's history
  const link = (go, text, nav, title) => `<a href="${L.api.esc(L.api.url(nav))}" data-ly-go="${go}"${title ? ` title="${L.api.esc(title)}"` : ''}>${text}</a>`;
  const TREE = { view: 'tree', diag: null, dpath: null, m: null, page: null, cam: null };

  function today(id, selected) {
    const device = selected ? L.api.entityLabel(selected) : '';
    if (id === 'deploy') {
      const util = (text) => (selected ? link('utilities', text, { ...TREE, page: 'utilities' }, `Open the Utilities tab of ${device}`) : text);
      return `<p>Not built yet as a whole. One part is real today, for a single device: on its page, ${util('Utilities › Update firmware…')} and ${util('Utilities › Update configuration…')} act on that device alone.</p>`
        + (selected ? `<p class="ly-device">Opens on the selected device: ${L.api.esc(device)}</p>` : '<p class="ly-device">Select a device in the tree to open its Utilities.</p>');
    }
    if (id === 'data') {
      return `<p>Not built yet. RobotDx keeps only its recent history, like a system monitor does; ${link('activity', 'Activity', { ...TREE, view: 'activity' })} shows it.</p>`;
    }
    return '<p>Not built yet.</p>';
  }

  function html(id) {
    const layer = LAYERS[id];
    const s = L.api.S;
    const selected = s.selKey ? null : s.sel;
    const diag = selected ? link('diagnostics', 'Diagnostics', { ...TREE, page: 'diagnostics' }, `Open the Diagnostics tab of ${L.api.entityLabel(selected)}`) : 'Diagnostics';
    return `<article class="ly-root" data-layer="${id}">
      <svg class="ly-ic" aria-hidden="true"><use href="#${ICONS[id]}"/></svg>
      <h1>${L.api.esc(layer.title)}</h1>
      <p class="ly-purpose">${L.api.esc(layer.purpose)}</p>
      <section class="ly-sec"><h2>What it will do</h2><ul>${layer.will.map((line) => `<li>${line}</li>`).join('')}</ul></section>
      <section class="ly-sec"><h2>It builds on</h2>
        <p>${link('devices', 'Device management', TREE)} (detect a device, read it and configure it), then ${diag} (health checks over each device's live and recent data).</p></section>
      <section class="ly-today"><h2>Today</h2>${today(id, selected)}</section>
    </article>`;
  }

  // app.js calls update(view) after every render; a layer id is the page's view, anything else hides nothing and draws nothing
  function update(view) {
    const host = document.getElementById('layers-host');
    if (!L.api || !host) return;
    if (!LAYERS[view]) { L.key = ''; return; }
    const drawn = html(view);
    // WHY compared: the page renders once a second; an unchanged page keeps its scroll and a link its focus
    if (L.key !== drawn) { L.key = drawn; host.innerHTML = drawn; }
  }

  document.addEventListener('click', (event) => {
    const a = event.target.closest && event.target.closest('#layers-host a[data-ly-go]');
    if (!a || !L.api || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    const go = a.dataset.lyGo;
    L.api.go(go === 'activity' ? { view: 'activity' } : go === 'devices' ? {} : { page: go });
  });

  window.IOLayers = {
    VIEWS: Object.keys(LAYERS),
    attach(api) { L.api = api; },
    update,
  };
}());
