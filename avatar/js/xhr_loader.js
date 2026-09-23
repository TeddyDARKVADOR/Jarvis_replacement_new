/**
 * xhr_loader.js — one patch, and the whole three.js loading ecosystem works.
 *
 * THE PROBLEM
 *   three.js r169 loads every file through `THREE.FileLoader`, which uses
 *   `fetch`. Under the `jarvis://` scheme the desktop panel serves from, fetch
 *   does not work: QtWebEngine 6.7 accepts a custom scheme's registration and
 *   keeps none of its flags, so the origin is never CORS-enabled.
 *   `client_desktop/ui/avatar_scheme.py` carries the measurement.
 *
 *   The cost is not one feature. It is `GLTFLoader` reading a model, and
 *   `TextureLoader` reading an external PNG, and `KTX2Loader` reading the Basis
 *   transcoder, and `DRACOLoader` reading its decoder — everything, because
 *   they all go through the same class. Working around it per loader means
 *   reimplementing each one and giving up compressed textures and compressed
 *   geometry, which is most of what "game ready" means on an asset store.
 *
 * THE FIX
 *   Replace `FileLoader.prototype.load` with the XMLHttpRequest implementation
 *   three.js itself shipped until r152. XHR on this scheme returns 200 —
 *   measured, not assumed. Every loader above it is then unchanged and unaware.
 *
 * WHY A MONKEY-PATCH IS THE RIGHT SHAPE HERE
 *   The alternative is a fork of three.js, or a per-loader workaround repeated
 *   five times and again for every loader added later. This is one function,
 *   in one file, that can be deleted the day Qt honours scheme flags — and
 *   until then it is the only place in the project that knows the transport is
 *   unusual.
 *
 * WHAT IS FAITHFULLY REPRODUCED
 *   The parts other loaders depend on: the shared in-flight map (so twenty
 *   materials asking for one texture cause one request), `responseType`,
 *   `mimeType`, `withCredentials`, `requestHeader`, progress events, and
 *   `THREE.Cache`. Leaving out the in-flight map in particular would turn a
 *   model with a repeated texture into a model that downloads it repeatedly.
 */

import * as THREE from 'three';

let patched = false;

export function useXhrLoading() {
  if (patched) return;
  patched = true;

  const loading = {};   // url -> callbacks en attente, partage entre appels

  THREE.FileLoader.prototype.load = function load(url, onLoad, onProgress, onError) {
    if (url === undefined) url = '';
    if (this.path !== undefined) url = this.path + url;
    url = this.manager.resolveURL(url);

    const cached = THREE.Cache.get(url);
    if (cached !== undefined) {
      this.manager.itemStart(url);
      // Asynchrone meme sur un cache chaud : un onLoad appele avant que load()
      // ait rendu la main casse tout appelant qui range le resultat APRES
      // l'appel, ce que fait la moitie des loaders de three.js.
      setTimeout(() => {
        if (onLoad) onLoad(cached);
        this.manager.itemEnd(url);
      }, 0);
      return cached;
    }

    if (loading[url] !== undefined) {
      loading[url].push({ onLoad, onProgress, onError });
      return;
    }
    loading[url] = [{ onLoad, onProgress, onError }];

    const request = new XMLHttpRequest();
    request.open('GET', url, true);

    const scope = this;

    request.addEventListener('load', function (event) {
      const response = this.response;
      const callbacks = loading[url];
      delete loading[url];

      // 0 est un succes sur file:// et sur les schemas personnalises : traiter
      // 0 comme une erreur rendrait ce chargeur inutilisable hors serveur, ce
      // qui est exactement le cas qu'il existe pour couvrir.
      if (this.status === 200 || this.status === 0) {
        if (this.status === 0) {
          console.warn('THREE.FileLoader: HTTP Status 0 recu (file:// ou schema local).');
        }
        THREE.Cache.add(url, response);
        for (const callback of callbacks) {
          if (callback.onLoad) callback.onLoad(response);
        }
        scope.manager.itemEnd(url);
      } else {
        const error = new Error(`HTTP ${this.status} sur ${url}`);
        for (const callback of callbacks) {
          if (callback.onError) callback.onError(error);
        }
        scope.manager.itemError(url);
        scope.manager.itemEnd(url);
      }
    });

    request.addEventListener('progress', (event) => {
      const callbacks = loading[url] || [];
      for (const callback of callbacks) {
        if (callback.onProgress) callback.onProgress(event);
      }
    });

    const fail = (event) => {
      const callbacks = loading[url] || [];
      delete loading[url];
      const error = new Error(`requete impossible : ${url}`);
      for (const callback of callbacks) {
        if (callback.onError) callback.onError(error);
      }
      scope.manager.itemError(url);
      scope.manager.itemEnd(url);
    };
    request.addEventListener('error', fail);
    request.addEventListener('abort', fail);

    if (this.responseType !== undefined) request.responseType = this.responseType;
    if (this.withCredentials !== undefined) request.withCredentials = this.withCredentials;
    if (request.overrideMimeType) {
      request.overrideMimeType(this.mimeType !== undefined ? this.mimeType : 'text/plain');
    }
    for (const header in this.requestHeader) {
      request.setRequestHeader(header, this.requestHeader[header]);
    }

    request.send(null);
    this.manager.itemStart(url);
    return request;
  };
}
