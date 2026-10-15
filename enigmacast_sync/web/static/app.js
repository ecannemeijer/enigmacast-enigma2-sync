(function () {
  var page = document.body.dataset.page;
  var main = document.getElementById("main");
  var csrf = document.querySelector('meta[name="csrf-token"]').content;
  document.querySelectorAll("nav a").forEach(function (link) {
    var name = link.dataset.nav;
    if (name === page || (page === "profile" && name === "profiles")) link.classList.add("active");
  });

  function get(url) {
    return fetch(url, { headers: { "Accept": "application/json" } }).then(function (response) {
      if (!response.ok) throw new Error("request failed");
      return response.json();
    });
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, "Accept": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (response) {
      return response.text().then(function (text) {
        var payload;
        try {
          payload = JSON.parse(text);
        } catch (error) {
          throw new Error("De server gaf geen geldig antwoord. Vernieuw de pagina en probeer opnieuw.");
        }
        if (!response.ok) throw new Error(payload.error || "request failed");
        return payload;
      });
    });
  }

  function when(ms) {
    if (ms == null) return "—";
    return new Date(ms).toLocaleString();
  }

  function bytes(value) {
    if (value == null) return "—";
    var units = ["B", "KB", "MB", "GB", "TB"];
    var size = value;
    var index = 0;
    while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
    return size.toFixed(index ? 1 : 0) + " " + units[index];
  }

  function duration(seconds) {
    if (seconds == null) return "—";
    var whole = Math.floor(seconds);
    var hours = Math.floor(whole / 3600);
    var minutes = Math.floor((whole % 3600) / 60);
    return hours + " u " + minutes + " min";
  }

  function status(on) {
    return on ? '<span class="ok">actief</span>' : '<span class="bad">gestopt</span>';
  }

  function card(label, value) {
    return '<article class="card"><span>' + label + '</span><strong>' + value + '</strong></article>';
  }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"]/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[char];
    });
  }

  if (page === "overview") {
    get("/api/overview").then(function (data) {
      var memory = data.memory ? bytes(data.memory.available) + " vrij van " + bytes(data.memory.total) : "niet beschikbaar";
      var store = data.disk ? bytes(data.disk.free) + " vrij van " + bytes(data.disk.total) : "niet beschikbaar";
      main.innerHTML = '<section class="grid">' +
        card("Sync API", status(data.api)) +
        card("Web Manager", status(data.web)) +
        card("Versie", esc(data.version)) +
        card("API-uptime", duration(data.apiUptime)) +
        card("Web-uptime", duration(data.webUptime)) +
        card("CPU", data.cpu == null ? "—" : esc(data.cpu) + "%") +
        card("RAM", memory) +
        card("Opslag", store) +
        card("Database", bytes(data.databaseBytes)) +
        card("Profielen", esc(data.profiles)) +
        card("Apparaten", esc(data.devices)) +
        card("Laatste synchronisatie", when(data.lastSyncMs)) +
        "</section>";
    }).catch(function () { main.textContent = "Overzicht kon niet worden geladen."; });
  }

  if (page === "server") {
    main.innerHTML = '<section class="panel"><h2>Sync API</h2><p id="api-state" class="muted">Status laden…</p>' +
      '<div class="actions"><button data-action="start">Start</button><button data-action="stop">Stop</button><button data-action="restart">Restart</button>' +
      '<button id="backup" class="ghost">Databaseback-up</button></div><p id="action-result" class="muted"></p></section>' +
      '<section class="panel"><h2>Wachtwoord</h2><p id="password-hint" class="muted"></p>' +
      '<form id="password-form" class="password-form">' +
      '<label for="pw-current">Huidig wachtwoord</label><input id="pw-current" type="password" autocomplete="current-password" required>' +
      '<label for="pw-new">Nieuw wachtwoord</label><input id="pw-new" type="password" autocomplete="new-password" required minlength="4">' +
      '<label for="pw-again">Herhaal nieuw wachtwoord</label><input id="pw-again" type="password" autocomplete="new-password" required minlength="4">' +
      '<button type="submit">Wachtwoord wijzigen</button></form><p id="pw-result" class="muted"></p></section>' +
      '<section class="panel"><h2>Versie</h2><p id="version" class="muted">Release-informatie laden…</p></section>' +
      '<section class="panel"><h2>Logs</h2><div class="actions"><button id="log-sync" class="ghost">Sync API</button><button id="log-web" class="ghost">Web Manager</button></div><pre id="log"></pre></section>';
    function refresh() {
      get("/api/server").then(function (data) {
        document.getElementById("api-state").innerHTML = "Sync API is " + status(data.api) + ". Web Manager is " + status(data.web) + " op poort " + esc(data.port) + ".";
        document.getElementById("password-hint").textContent = data.defaultPassword
          ? "Het wachtwoord is nog admin. Kies hier een eigen wachtwoord."
          : "Het dashboardwachtwoord geldt alleen voor deze webpagina.";
      });
    }
    function showLog(which) {
      get("/api/logs?which=" + which).then(function (data) {
        document.getElementById("log").textContent = data.lines.length ? data.lines.join("\n") : "Geen logregels.";
      });
    }
    document.querySelectorAll("[data-action]").forEach(function (button) {
      button.addEventListener("click", function () {
        post("/api/server", { action: button.dataset.action }).then(function (result) {
          document.getElementById("action-result").textContent = button.dataset.action + " afgerond (code " + result.exit + ").";
          refresh();
          showLog("sync");
        }).catch(function () {
          document.getElementById("action-result").textContent = "De actie is niet uitgevoerd.";
        });
      });
    });
    document.getElementById("backup").addEventListener("click", function () {
      post("/api/backup", {}).then(function (result) {
        document.getElementById("action-result").textContent = "Back-up op de ontvanger: " + result.path;
      }).catch(function () {
        document.getElementById("action-result").textContent = "Back-up is niet gemaakt.";
      });
    });
    document.getElementById("password-form").addEventListener("submit", function (event) {
      event.preventDefault();
      var chosen = document.getElementById("pw-new").value;
      var again = document.getElementById("pw-again").value;
      var result = document.getElementById("pw-result");
      if (chosen !== again) {
        result.textContent = "De nieuwe wachtwoorden zijn niet gelijk.";
        return;
      }
      post("/api/password", {
        current: document.getElementById("pw-current").value,
        password: chosen,
        confirm: again
      }).then(function () {
        document.getElementById("password-form").reset();
        result.textContent = "Het wachtwoord is gewijzigd.";
        refresh();
      }).catch(function (error) {
        result.textContent = error.message || "Het wachtwoord is niet gewijzigd.";
      });
    });
    document.getElementById("log-sync").addEventListener("click", function () { showLog("sync"); });
    document.getElementById("log-web").addEventListener("click", function () { showLog("web"); });
    get("/api/update").then(function (info) {
      var text = "Geïnstalleerd: " + info.version + ".";
      if (info.latest) text += info.update ? " Nieuwere release: " + info.latest + "." : " Dit is de nieuwste release.";
      else text += " Update-informatie is nu niet beschikbaar.";
      document.getElementById("version").textContent = text;
    });
    refresh();
    showLog("sync");
  }

  if (page === "profiles") {
    var state = { q: "", sort: "name", page: 1 };
    main.innerHTML = '<section class="panel"><div class="toolbar"><input id="q" placeholder="Zoeken op naam of uuid">' +
      '<select id="sort"><option value="name">Naam</option><option value="updated">Laatste synchronisatie</option>' +
      '<option value="favorites">Favorieten</option><option value="vod">VOD</option><option value="episodes">Series</option></select></div>' +
      '<div id="table"></div></section>';
    function render(data) {
      var rows = data.profiles.map(function (row) {
        return "<tr><td><a href=\"/profiles/" + encodeURIComponent(row.uuid) + "\">" + esc(row.name) + "</a></td><td>" + esc(row.uuid) +
          "</td><td>" + esc(row.favorites) + "</td><td>" + esc(row.vod) + "</td><td>" + esc(row.episodes) +
          "</td><td>—</td><td>" + when(row.updated) + "</td></tr>";
      }).join("");
      var pages = Math.max(1, Math.ceil(data.total / data.pageSize));
      document.getElementById("table").innerHTML = '<table><thead><tr><th>Naam</th><th>UUID</th><th>Favorieten</th><th>VOD-voortgang</th>' +
        '<th>Series en afleveringen</th><th>Kijkgeschiedenis</th><th>Laatste synchronisatie</th></tr></thead><tbody>' +
        (rows || '<tr><td colspan="7" class="empty">Geen profielen.</td></tr>') + "</tbody></table>" +
        '<div class="pager"><button id="prev" class="ghost">Vorige</button><span>' + data.page + " / " + pages + '</span><button id="next" class="ghost">Volgende</button></div>';
      document.getElementById("prev").disabled = data.page <= 1;
      document.getElementById("next").disabled = data.page >= pages;
      document.getElementById("prev").onclick = function () { state.page -= 1; load(); };
      document.getElementById("next").onclick = function () { state.page += 1; load(); };
    }
    function load() {
      get("/api/profiles?q=" + encodeURIComponent(state.q) + "&sort=" + encodeURIComponent(state.sort) + "&page=" + state.page).then(render);
    }
    document.getElementById("q").addEventListener("input", function (event) {
      state.q = event.target.value;
      state.page = 1;
      load();
    });
    document.getElementById("sort").addEventListener("change", function (event) {
      state.sort = event.target.value;
      state.page = 1;
      load();
    });
    load();
  }

  if (page === "profile") {
    get("/api/profiles/" + encodeURIComponent(document.body.dataset.id)).then(function (data) {
      var items = data.items.map(function (item) {
        return "<tr><td>" + esc(item.entity) + "</td><td>" + esc(item.kind) + "</td><td>" + esc(item.title) + "</td><td>" + when(item.updatedMs) + "</td></tr>";
      }).join("");
      main.innerHTML = '<section class="panel"><p><a href="/profiles">Profielen</a></p><h2>' + esc(data.name) + "</h2>" +
        "<p class=\"muted\">" + esc(data.uuid) + "</p><div class=\"grid\">" +
        card("Favorieten", esc(data.favorites)) + card("VOD-voortgang", esc(data.vod)) +
        card("Series en afleveringen", esc(data.episodes)) + card("Kijkgeschiedenis", "niet bijgehouden") +
        card("Laatste synchronisatie", when(data.updatedMs)) + "</div></section>" +
        '<section class="panel"><h2>Records</h2><table><thead><tr><th>Soort</th><th>Kind</th><th>Titel</th><th>Bijgewerkt</th></tr></thead><tbody>' +
        (items || '<tr><td colspan="4" class="empty">Geen favorieten of voortgang.</td></tr>') + "</tbody></table></section>";
    }).catch(function () { main.textContent = "Dit profiel is niet gevonden."; });
  }

  if (page === "devices") {
    get("/api/devices").then(function (data) {
      var rows = data.devices.map(function (row) {
        var role = row.role === "admin" ? "beheerder" : "client";
        var pairing = row.revoked ? "ingetrokken" : "gekoppeld";
        return "<tr><td>" + esc(row.name) + "</td><td>" + role + "</td><td>—</td><td>" + when(row.lastSyncMs) +
          "</td><td>—</td><td>" + pairing + "</td></tr>";
      }).join("");
      main.innerHTML = '<section class="panel"><p class="muted">Laatste verbinding en app-versie worden door de Sync API niet opgeslagen.</p>' +
        "<table><thead><tr><th>Apparaat</th><th>Rol</th><th>Laatste verbinding</th><th>Laatste synchronisatie</th><th>App-versie</th><th>Pairing</th></tr></thead><tbody>" +
        (rows || '<tr><td colspan="6" class="empty">Geen apparaten.</td></tr>') + "</tbody></table></section>";
    });
  }

  if (page === "sync") {
    get("/api/sync").then(function (data) {
      var rows = data.events.map(function (row) {
        return "<tr><td>" + when(row.updatedMs) + "</td><td>" + esc(row.device) + "</td><td>" + esc(row.profileId) +
          "</td><td>" + esc(row.entity) + "</td><td>" + esc(row.op) + "</td><td class=\"ok\">geslaagd</td></tr>";
      }).join("");
      var log = data.log.filter(function (line) { return /error|fail|exception/i.test(line); });
      main.innerHTML = '<section class="panel"><h2>Recente synchronisaties</h2><table><thead><tr><th>Tijd</th><th>Apparaat</th><th>Profiel</th><th>Entiteit</th><th>Actie</th><th>Resultaat</th></tr></thead><tbody>' +
        (rows || '<tr><td colspan="6" class="empty">Nog geen synchronisaties.</td></tr>') + "</tbody></table></section>" +
        '<section class="panel"><h2>Conflicten en wachtrij</h2><p class="muted">Geweigerde conflicten en een wachtrij worden niet opgeslagen. Alleen toegepaste wijzigingen staan hierboven.</p></section>' +
        '<section class="panel"><h2>Fouten in het Sync-log</h2><pre>' + esc(log.length ? log.join("\n") : "Geen foutregels.") + "</pre></section>";
    });
  }
})();
