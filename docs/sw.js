// Radar de Investimentos: busca sempre a versao nova; sem internet, usa a ultima guardada.
var CACHE = "radar-v1";
var BASE = ["./", "index.html", "dados.json", "detalhes.json", "manifest.webmanifest", "icone.svg", "icone-192.png"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(BASE); }).catch(function () { /* instala mesmo sem tudo */ }));
  self.skipWaiting();
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (nomes) {
    return Promise.all(nomes.filter(function (n) { return n !== CACHE; }).map(function (n) { return caches.delete(n); }));
  }));
  self.clients.claim();
});

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== self.location.origin) return;
  e.respondWith(fetch(req).then(function (resp) {
    if (resp && resp.ok) {
      var copia = resp.clone();
      caches.open(CACHE).then(function (c) { c.put(req, copia); });
    }
    return resp;
  }).catch(function () {
    return caches.match(req, { ignoreSearch: true }).then(function (r) { return r || caches.match("index.html"); });
  }));
});
