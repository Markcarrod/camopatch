/**
 * stealth_patch.js
 * ================
 * Covers the 4 remaining fingerprinting surfaces that cannot be addressed
 * through Firefox user-prefs alone and require JavaScript prototype patching:
 *
 *  1. OffscreenCanvas / Worker context – canvas noise isolation & Navigator/Storage consistency
 *  2. Cross-origin iframe navigator consistency
 *  3. WebGPU adapter spoofing (aligned with the WebGL vendor/renderer)
 *  4. Font metric (measureText / getBoundingClientRect) noise injection
 *
 * It also handles:
 *  - navigator.deviceMemory spoofing (correlated with GPU tier)
 *  - navigator.userAgentData platformVersion / fullBuild overrides (forcing Windows 10/11 alignment)
 *  - navigator.storage.estimate() quota randomization (stable, non-round values)
 *  - permissions.query() consistency
 *  - Math spoofing (Math.sin, Math.cos, Math.tan micro-perturbations)
 *  - Audio spoofing (AudioBuffer.getChannelData, copyFromChannel, and AnalyserNode frequency transformations)
 *  - DOMRect spoofing (boundingClientRect sub-pixel micro-jitter)
 *  - Canvas pixel & WebGL readPixels spoofing
 *  - Service Worker interception for full worker-scope consistency
 *
 * Inject this with:
 *   context.add_init_script(path="stealth_patch.js")          (Playwright)
 *   page.add_init_script(path="stealth_patch.js")             (Playwright)
 */
(() => {
  'use strict';

  // If we are in the main world, window.__camou_main_world will be true.
  if (typeof window !== 'undefined' && window.__camou_main_world) {
    __camou_main_patch();
    return;
  }

  // If we are in a worker context (where window is undefined), run the patch directly.
  if (typeof window === 'undefined') {
    __camou_main_patch();
    return;
  }

  // If we are in the Playwright sandbox context, inject into the main world.
  const profile = window.__camou_profile || {};
  
  const inject = () => {
    const target = document.head || document.documentElement;
    if (target) {
      try {
        const script = document.createElement('script');
        script.textContent = `
          window.__camou_profile = ${JSON.stringify(profile)};
          window.__camou_main_world = true;
          (${__camou_main_patch.toString()})();
        `;
        target.appendChild(script);
        script.remove();
        return true;
      } catch (e) {
        console.error('[camou-patch] Script injection failed:', e);
      }
    }
    return false;
  };

  if (!inject()) {
    const observer = new MutationObserver((mutations, obs) => {
      if (inject()) {
        obs.disconnect();
      }
    });
    observer.observe(document, { childList: true, subtree: true });
  }
  return;

  function __camou_main_patch() {
    if (typeof window === 'undefined') return;
    if (window.__camou_patched) return;
    Object.defineProperty(window, '__camou_patched', { value: true, configurable: false });

    // -------------------------------------------------------------------------
    // Resolve profile constants (passed from python wrapper) or use sane defaults
    // -------------------------------------------------------------------------
    const profile = window.__camou_profile || {};
  const platformVersion = profile.platformVersion || "10.0.0"; // "10.0.0" (Win10)
  const fullBuild       = profile.fullBuild       || "19045.3155";
  const storageQuota    = profile.storageQuota    || 61234567890;

  // -------------------------------------------------------------------------
  // Seeded deterministic pseudo-random number generator
  // -------------------------------------------------------------------------
  function createRng(seedStr) {
    let h = 2166136261 >>> 0;
    for (let i = 0; i < seedStr.length; i++) {
      h ^= seedStr.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    return function() {
      h = Math.imul(h ^ (h >>> 16), 2246822507) >>> 0;
      h = Math.imul(h ^ (h >>> 13), 3266489909) >>> 0;
      h = (h ^ (h >>> 16)) >>> 0;
      return h / 4294967296;
    };
  }
  const profileRng = createRng(JSON.stringify(profile));

  // -------------------------------------------------------------------------
  // Utility: make a function's .toString() look like a native built-in.
  // -------------------------------------------------------------------------
  const _real_toString = Function.prototype.toString;
  function nativify(fn, name) {
    const native_str = `function ${name}() { [native code] }`;
    try {
      Object.defineProperty(fn, 'name', { value: name, configurable: true });
    } catch (_) {}
    fn.toString = function() { return native_str; };
    fn.toString.toString = function() { return _real_toString.call(_real_toString); };
    return fn;
  }

  // -------------------------------------------------------------------------
  // Utility: safe defineProperty that silently fails if already non-configurable
  // -------------------------------------------------------------------------
  function defineProp(obj, prop, descriptor) {
    try {
      const existing = Object.getOwnPropertyDescriptor(obj, prop);
      if (existing && !existing.configurable) return false;
      Object.defineProperty(obj, prop, descriptor);
      return true;
    } catch (_) {
      return false;
    }
  }

  // =========================================================================
  // 0. Math Spoofing
  //    Trigonometric micro-perturbations at the 16th decimal place.
  // =========================================================================
  (() => {
    const seed = profileRng();
    const perturbVal = (seed - 0.5) * 1e-16;

    const _origSin = Math.sin;
    const _origCos = Math.cos;
    const _origTan = Math.tan;

    defineProp(Math, 'sin', {
      value: nativify(function sin(x) {
        const v = _origSin(x);
        return (typeof v === 'number' && !isNaN(v) && isFinite(v)) ? v + perturbVal * (x % 5 || 1) : v;
      }, 'sin'),
      writable: true, configurable: true
    });

    defineProp(Math, 'cos', {
      value: nativify(function cos(x) {
        const v = _origCos(x);
        return (typeof v === 'number' && !isNaN(v) && isFinite(v)) ? v + perturbVal * (x % 3 || 1) : v;
      }, 'cos'),
      writable: true, configurable: true
    });

    defineProp(Math, 'tan', {
      value: nativify(function tan(x) {
        const v = _origTan(x);
        return (typeof v === 'number' && !isNaN(v) && isFinite(v)) ? v + perturbVal : v;
      }, 'tan'),
      writable: true, configurable: true
    });
  })();

  // =========================================================================
  // 1. Audio Spoofing
  //    Injects a deterministic variance based on profile seeds.
  // =========================================================================
  (() => {
    const audioSeed = profile.audioSeed !== undefined ? profile.audioSeed : (Math.floor(profileRng() * 1000000) + 1);
    
    // Part A: AudioBuffer channel data spoofing (OfflineAudioContext)
    if (typeof AudioBuffer !== 'undefined') {
      const _origGetChannelData = AudioBuffer.prototype.getChannelData;
      const _origCopyFromChannel = AudioBuffer.prototype.copyFromChannel;

      function transformAudioBuffer(bufferArray, seed) {
        if (!bufferArray || bufferArray.length === 0) return;
        let state = seed;
        for (let i = 0; i < bufferArray.length; i++) {
          state = (state * 1664525 + 1013904223) >>> 0;
          const normalized = state / 4294967296;
          const multiplier = 0.998 + normalized * 0.004;
          bufferArray[i] *= multiplier;
        }
      }

      defineProp(AudioBuffer.prototype, 'getChannelData', {
        value: nativify(function getChannelData(channel) {
          const data = _origGetChannelData.call(this, channel);
          if (data && !data.__modified) {
            transformAudioBuffer(data, audioSeed + channel);
            Object.defineProperty(data, '__modified', { value: true, enumerable: false });
          }
          return data;
        }, 'getChannelData'),
        writable: true, configurable: true
      });

      defineProp(AudioBuffer.prototype, 'copyFromChannel', {
        value: nativify(function copyFromChannel(destination, channelNumber, startInChannel) {
          _origCopyFromChannel.call(this, destination, channelNumber, startInChannel);
          if (destination) {
            transformAudioBuffer(destination, audioSeed + channelNumber + (startInChannel || 0));
          }
        }, 'copyFromChannel'),
        writable: true, configurable: true
      });
    }

    // Part B: AnalyserNode frequency analysis spoofing (Real-time AudioContext)
    if (typeof AnalyserNode !== 'undefined') {
      const _origGBFD = AnalyserNode.prototype.getByteFrequencyData;
      defineProp(AnalyserNode.prototype, 'getByteFrequencyData', {
        value: nativify(function getByteFrequencyData(array) {
          _origGBFD.call(this, array);
          if (array && array.length > 0) {
            for (let i = 0; i < array.length; i++) {
              if (array[i] > 0) {
                // Add tiny deterministic offset to break static sum check
                array[i] = Math.min(255, Math.max(0, array[i] + ((audioSeed + i) % 2 === 0 ? 1 : -1)));
              }
            }
          }
        }, 'getByteFrequencyData'),
        writable: true, configurable: true
      });

      const _origGFFD = AnalyserNode.prototype.getFloatFrequencyData;
      defineProp(AnalyserNode.prototype, 'getFloatFrequencyData', {
        value: nativify(function getFloatFrequencyData(array) {
          _origGFFD.call(this, array);
          if (array && array.length > 0) {
            for (let i = 0; i < array.length; i++) {
              array[i] += ((audioSeed + i) % 2 === 0 ? 0.05 : -0.05);
            }
          }
        }, 'getFloatFrequencyData'),
        writable: true, configurable: true
      });
    }
  })();

  // =========================================================================
  // 2. DOMRect Spoofing
  //    Sub-pixel boundingClientRect micro-jitter.
  // =========================================================================
  (() => {
    if (typeof Element === 'undefined') return;

    const rectJitter = (profileRng() - 0.5) * 0.0001; 
    const _origGetBoundingClientRect = Element.prototype.getBoundingClientRect;

    defineProp(Element.prototype, 'getBoundingClientRect', {
      value: nativify(function getBoundingClientRect() {
        const rect = _origGetBoundingClientRect.call(this);
        return {
          x: rect.x + rectJitter,
          y: rect.y + rectJitter,
          top: rect.top + rectJitter,
          left: rect.left + rectJitter,
          right: rect.right + rectJitter,
          bottom: rect.bottom + rectJitter,
          width: rect.width + rectJitter,
          height: rect.height + rectJitter,
          toJSON: function() {
            return {
              x: this.x, y: this.y, top: this.top, left: this.left,
              right: this.right, bottom: this.bottom, width: this.width, height: this.height
            };
          }
        };
      }, 'getBoundingClientRect'),
      writable: true, configurable: true
    });
  })();

  // =========================================================================
  // 3. Canvas Pixel & WebGL readPixels Spoofing
  // =========================================================================
  (() => {
    const canvasSeed = profile.canvasSeed !== undefined ? (profile.canvasSeed % 1000 + 1) : (Math.floor(profileRng() * 1000) + 1);

    // Part A: 2D Canvas dynamic perturbation right before export
    function perturbCanvas(canvas) {
      try {
        const ctx = canvas.getContext('2d');
        if (ctx) {
          const _origFillStyle = ctx.fillStyle;
          const _origGlobalAlpha = ctx.globalAlpha;
          ctx.globalAlpha = 0.005; // extremely faint, invisible
          ctx.fillStyle = `rgb(${canvasSeed % 256}, ${(canvasSeed * 7) % 256}, ${(canvasSeed * 13) % 256})`;
          ctx.fillRect(0, 0, 1, 1);
          ctx.fillStyle = _origFillStyle;
          ctx.globalAlpha = _origGlobalAlpha;
        }
      } catch (_) {}
    }

    if (typeof HTMLCanvasElement !== 'undefined') {
      const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
      defineProp(HTMLCanvasElement.prototype, 'toDataURL', {
        value: nativify(function toDataURL(type, quality) {
          perturbCanvas(this);
          return _origToDataURL.call(this, type, quality);
        }, 'toDataURL'),
        writable: true, configurable: true
      });

      const _origToBlob = HTMLCanvasElement.prototype.toBlob;
      defineProp(HTMLCanvasElement.prototype, 'toBlob', {
        value: nativify(function toBlob(callback, type, quality) {
          perturbCanvas(this);
          return _origToBlob.call(this, callback, type, quality);
        }, 'toBlob'),
        writable: true, configurable: true
      });
    }

    // Part B: WebGL readPixels interception to alter GPU pixel checksum
    function patchReadPixels(proto) {
      if (!proto || !proto.readPixels) return;
      const _origReadPixels = proto.readPixels;
      defineProp(proto, 'readPixels', {
        value: nativify(function readPixels(x, y, width, height, format, type, pixels) {
          _origReadPixels.call(this, x, y, width, height, format, type, pixels);
          if (pixels && pixels.length > 0) {
            const mid = Math.floor(pixels.length / 2) & ~3; // Align to 4-byte boundary
            if (mid < pixels.length) {
              pixels[mid] = Math.min(255, Math.max(0, pixels[mid] + (canvasSeed % 2 === 0 ? 1 : -1)));
            }
          }
        }, 'readPixels'),
        writable: true, configurable: true
      });
    }

    if (typeof WebGLRenderingContext !== 'undefined') patchReadPixels(WebGLRenderingContext.prototype);
    if (typeof WebGL2RenderingContext !== 'undefined') patchReadPixels(WebGL2RenderingContext.prototype);
  })();

  // =========================================================================
  // 4. navigator.deviceMemory — correlated with GPU tier (main thread + iframes)
  // =========================================================================
  (() => {
    if (typeof Navigator === 'undefined') return;

    function inferDeviceMemory() {
      try {
        const canvas = document.createElement('canvas');
        const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
        if (!gl) return 8;
        const ext = gl.getExtension('WEBGL_debug_renderer_info');
        if (!ext) return 8;
        const renderer = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) || '';
        if (/RTX (3[5-9]\d{2}|4\d{3}|[5-9]\d{3})|RX (6[789]\d{2}|7\d{3})/i.test(renderer)) return 16;
        if (/GTX 16\d{2}|RTX [23]\d{3}|RX [56]\d{3}|Iris.+Xe/i.test(renderer)) return 8;
        if (/HD (5[0-9]{2}|6[0-4][0-9])|UHD 6[0-3][0-9]/i.test(renderer)) return 4;
        return 8;
      } catch (_) {
        return 8;
      }
    }

    const mem = profile.deviceMemory || inferDeviceMemory();
    defineProp(Navigator.prototype, 'deviceMemory', {
      get: nativify(function() { return mem; }, 'get deviceMemory'),
      configurable: true,
      enumerable: true,
    });
  })();

  // =========================================================================
  // 4.1. navigator.doNotTrack — disable DNT tracking signal
  // =========================================================================
  (() => {
    if (typeof Navigator === 'undefined') return;
    defineProp(Navigator.prototype, 'doNotTrack', {
      get: nativify(function() { return 'unspecified'; }, 'get doNotTrack'),
      configurable: true,
      enumerable: true,
    });
  })();


  // =========================================================================
  // 5. userAgentData WINDOWS 10/11 ALIGNMENT
  // =========================================================================
  (() => {
    if (typeof navigator === 'undefined') return;

    const uad = navigator.userAgentData;
    if (uad) {
      if ('platformVersion' in uad) {
        defineProp(uad, 'platformVersion', {
          get: nativify(() => platformVersion, 'get platformVersion'),
          configurable: true, enumerable: true
        });
      }

      const _realGHEV = uad.getHighEntropyValues.bind(uad);
      const patchedGHEV = nativify(async function getHighEntropyValues(hints) {
        const res = await _realGHEV(hints);
        if (res) {
          if (hints.includes('platformVersion') || hints.includes('uaFullVersion')) {
            res.platformVersion = platformVersion;
            res.uaFullVersion   = fullBuild;
          }
        }
        return res;
      }, 'getHighEntropyValues');

      defineProp(Object.getPrototypeOf(uad), 'getHighEntropyValues', {
        value: patchedGHEV,
        configurable: true, writable: true
      });
    }
  })();

  // =========================================================================
  // 6. STORAGE QUOTA ESTIMATE RANDOMIZATION (navigator.storage.estimate)
  // =========================================================================
  (() => {
    if (typeof navigator === 'undefined' || !navigator.storage) return;

    const proto = Object.getPrototypeOf(navigator.storage);
    if (proto && proto.estimate) {
      const _realEstimate = proto.estimate.bind(navigator.storage);
      const patchedEstimate = nativify(async function estimate() {
        const est = await _realEstimate();
        return {
          quota: storageQuota,
          usage: est ? (est.usage || 0) : 0
        };
      }, 'estimate');

      defineProp(proto, 'estimate', {
        value: patchedEstimate,
        configurable: true, writable: true
      });
    }
  })();

  // =========================================================================
  // 7. WEBGPU SPOOFING
  // =========================================================================
  (() => {
    if (typeof navigator === 'undefined') return;

    function getWebGLIdentity() {
      try {
        const canvas = document.createElement('canvas');
        const gl = canvas.getContext('webgl2') || canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
        if (!gl) return null;
        const ext = gl.getExtension('WEBGL_debug_renderer_info');
        if (!ext) return null;
        const vendor   = gl.getParameter(ext.UNMASKED_VENDOR_WEBGL);
        const renderer = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
        return { vendor, renderer };
      } catch (_) {
        return null;
      }
    }

    function webglToWebGPUAdapter(webglVendor, webglRenderer) {
      let gpuVendor = 'unknown';
      let gpuArchitecture = 'unknown';
      let gpuDevice = 'unknown';
      let gpuDescription = webglRenderer;

      if (/NVIDIA|GeForce/.test(webglRenderer)) {
        gpuVendor = 'nvidia';
        if (/RTX 4[0-9]{3}/.test(webglRenderer)) {
          gpuArchitecture = 'ada';
        } else if (/RTX 3[0-9]{3}/.test(webglRenderer)) {
          gpuArchitecture = 'ampere';
        } else if (/RTX 2[0-9]{3}|GTX 16[0-9]{2}/.test(webglRenderer)) {
          gpuArchitecture = 'turing';
        } else {
          gpuArchitecture = 'pascal';
        }
        const m = webglRenderer.match(/GeForce ([\w\s]+?) Direct3D/);
        gpuDevice = m ? m[1].trim() : webglRenderer;

      } else if (/AMD|Radeon/.test(webglRenderer)) {
        gpuVendor = 'amd';
        if (/RX 7[0-9]{3}/.test(webglRenderer)) {
          gpuArchitecture = 'rdna3';
        } else if (/RX 6[0-9]{3}/.test(webglRenderer)) {
          gpuArchitecture = 'rdna2';
        } else if (/RX 5[0-9]{3}/.test(webglRenderer)) {
          gpuArchitecture = 'rdna1';
        } else {
          gpuArchitecture = 'gcn';
        }
        const m = webglRenderer.match(/Radeon ([\w\s]+?) Direct3D/);
        gpuDevice = m ? m[1].trim() : webglRenderer;

      } else if (/Intel/.test(webglRenderer)) {
        gpuVendor = 'intel';
        if (/Iris.+Xe/.test(webglRenderer)) {
          gpuArchitecture = 'xe';
        } else if (/UHD/.test(webglRenderer)) {
          gpuArchitecture = 'gen12';
        } else {
          gpuArchitecture = 'gen9';
        }
        const m = webglRenderer.match(/Intel\(R\) ([\w()\s]+?) Direct3D/);
        gpuDevice = m ? m[1].trim() : webglRenderer;
      }

      return {
        vendor: gpuVendor,
        architecture: gpuArchitecture,
        device: gpuDevice,
        description: gpuDescription
      };
    }

    if (typeof navigator.gpu !== 'undefined') {
      const identity = getWebGLIdentity();
      if (!identity) return;

      const adapterInfo = webglToWebGPUAdapter(identity.vendor, identity.renderer);

      const mockAdapterInfo = Object.freeze({
        vendor:       adapterInfo.vendor,
        architecture: adapterInfo.architecture,
        device:       adapterInfo.device,
        description:  adapterInfo.description,
      });

      const _realGPU = navigator.gpu;
      const _realRequestAdapter = _realGPU.requestAdapter.bind(_realGPU);

      const patchedRequestAdapter = nativify(async function(options) {
        const realAdapter = await _realRequestAdapter(options);
        if (!realAdapter) return null;

        defineProp(realAdapter, 'requestAdapterInfo', {
          value: nativify(async function() {
            return mockAdapterInfo;
          }, 'requestAdapterInfo'),
          configurable: true,
          writable: true,
        });

        return realAdapter;
      }, 'requestAdapter');

      defineProp(_realGPU, 'requestAdapter', {
        value: patchedRequestAdapter,
        configurable: true,
        writable: true,
      });
    }
  })();

  // =========================================================================
  // 8. FONT METRIC FINGERPRINTING — measureText noise injection
  // =========================================================================
  (() => {
    if (typeof CanvasRenderingContext2D === 'undefined') return;

    const seed = (() => {
      if (profile.canvasSeed !== undefined) {
        return ((profile.canvasSeed % 5000) / 1000) - 2.5;
      }
      const s = (screen.width * 31337 + screen.height * 1337 + screen.colorDepth * 17) & 0xFFFFFF;
      return ((s % 1000) / 1000) - 0.5;
    })();

    const _realMeasureText = CanvasRenderingContext2D.prototype.measureText;

    const patchedMeasureText = nativify(function measureText(text) {
      const real = _realMeasureText.call(this, text);
      const noise = seed * (0.1 + (text.length % 7) * 0.01);
      const patchedResult = Object.create(Object.getPrototypeOf(real));

      for (const key of Object.getOwnPropertyNames(Object.getPrototypeOf(real))) {
        if (key === 'constructor') continue;
        try {
          const desc = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(real), key);
          if (desc && typeof desc.get === 'function') {
            Object.defineProperty(patchedResult, key, {
              get: () => {
                const val = desc.get.call(real);
                if (typeof val === 'number' && key.toLowerCase().includes('width')) {
                  return val + noise;
                }
                return val;
              },
              configurable: true,
              enumerable: true,
            });
          }
        } catch (_) {}
      }
      return patchedResult;
    }, 'measureText');

    defineProp(CanvasRenderingContext2D.prototype, 'measureText', {
      value: patchedMeasureText,
      configurable: true,
      writable: true,
    });
  })();

  // =========================================================================
  // 9. CROSS-ORIGIN IFRAME NAVIGATOR & STORAGE CONSISTENCY
  // =========================================================================
  (() => {
    if (typeof navigator === 'undefined') return;

    if (window === window.top) return;

    let topHardwareConcurrency = null;
    let topDeviceMemory = null;
    let topLanguages = null;
    let topMaxTouchPoints = null;

    try {
      topHardwareConcurrency = window.top.navigator.hardwareConcurrency;
      topDeviceMemory        = window.top.navigator.deviceMemory;
      topLanguages           = window.top.navigator.languages;
      topMaxTouchPoints      = window.top.navigator.maxTouchPoints;
    } catch (_) {
      return;
    }

    if (topHardwareConcurrency !== null) {
      defineProp(Navigator.prototype, 'hardwareConcurrency', {
        get: nativify(() => topHardwareConcurrency, 'get hardwareConcurrency'),
        configurable: true, enumerable: true,
      });
    }
    if (topDeviceMemory !== null && 'deviceMemory' in navigator) {
      defineProp(Navigator.prototype, 'deviceMemory', {
        get: nativify(() => topDeviceMemory, 'get deviceMemory'),
        configurable: true, enumerable: true,
      });
    }
    if (topMaxTouchPoints !== null) {
      defineProp(Navigator.prototype, 'maxTouchPoints', {
        get: nativify(() => topMaxTouchPoints, 'get maxTouchPoints'),
        configurable: true, enumerable: true,
      });
    }
    if (topLanguages !== null && Array.isArray(topLanguages)) {
      const frozenLanguages = Object.freeze([...topLanguages]);
      defineProp(Navigator.prototype, 'languages', {
        get: nativify(() => frozenLanguages, 'get languages'),
        configurable: true, enumerable: true,
      });
    }

    const uad = navigator.userAgentData;
    if (uad && 'platformVersion' in uad) {
      defineProp(uad, 'platformVersion', {
        get: nativify(() => platformVersion, 'get platformVersion'),
        configurable: true, enumerable: true
      });
    }
  })();

  // =========================================================================
  // 10. WORKER CONTEXT CONSISTENCY — Worker & SharedWorker wrapping
  // =========================================================================
  const workerPreamble = `
(function() {
  'use strict';

  // ---- Navigator overrides in Worker scope ----
  try {
    const _nav = self.navigator;
    if (_nav) {
      Object.defineProperty(_nav, 'hardwareConcurrency', {
        get: function() { return ${navigator.hardwareConcurrency}; },
        configurable: true, enumerable: true
      });
      if ('deviceMemory' in _nav) {
        Object.defineProperty(_nav, 'deviceMemory', {
          get: function() { return ${('deviceMemory' in navigator) ? navigator.deviceMemory : 8}; },
          configurable: true, enumerable: true
        });
      }
      if ('maxTouchPoints' in _nav) {
        Object.defineProperty(_nav, 'maxTouchPoints', {
          get: function() { return ${navigator.maxTouchPoints}; },
          configurable: true, enumerable: true
        });
      }
      Object.defineProperty(_nav, 'language', {
        get: function() { return '${navigator.language}'; },
        configurable: true, enumerable: true
      });
      Object.defineProperty(_nav, 'languages', {
        get: function() { return Object.freeze(${JSON.stringify([...navigator.languages])}); },
        configurable: true, enumerable: true
      });

      if (_nav.userAgentData) {
        Object.defineProperty(_nav.userAgentData, 'platformVersion', {
          get: function() { return '${platformVersion}'; },
          configurable: true, enumerable: true
        });
      }
    }
  } catch(_) {}

  // ---- Storage Estimate worker consistency ----
  try {
    if (self.navigator && self.navigator.storage) {
      const proto = Object.getPrototypeOf(self.navigator.storage);
      if (proto && proto.estimate) {
        proto.estimate = function estimate() {
          return Promise.resolve({
            quota: ${storageQuota},
            usage: 0
          });
        };
      }
    }
  } catch(_) {}

  // ---- OffscreenCanvas measureText noise ----
  try {
    if (typeof OffscreenCanvasRenderingContext2D !== 'undefined') {
      const _seed = ${profile.canvasSeed !== undefined ? (profile.canvasSeed % 5000) / 1000 - 2.5 : `(function() {
        const s = (${screen.width} * 31337 + ${screen.height} * 1337 + ${screen.colorDepth} * 17) & 0xFFFFFF;
        return ((s % 1000) / 1000) - 0.5;
      })()`};

      const _realMT = OffscreenCanvasRenderingContext2D.prototype.measureText;
      Object.defineProperty(OffscreenCanvasRenderingContext2D.prototype, 'measureText', {
        value: function measureText(text) {
          const real = _realMT.call(this, text);
          const noise = _seed * (0.1 + (text.length % 7) * 0.01);
          const proto = Object.getPrototypeOf(real);
          const patched = Object.create(proto);
          for (const key of Object.getOwnPropertyNames(proto)) {
            if (key === 'constructor') continue;
            try {
              const desc = Object.getOwnPropertyDescriptor(proto, key);
              if (desc && typeof desc.get === 'function') {
                Object.defineProperty(patched, key, {
                  get: (function(d) { return function() {
                    const v = d.get.call(real);
                    if (typeof v === 'number' && key.toLowerCase().includes('width')) return v + noise;
                    return v;
                  }; })(desc),
                  configurable: true, enumerable: true
                });
              }
            } catch(_) {}
          }
          return patched;
        },
        configurable: true, writable: true
      });
    }
  } catch(_) {}

  // ---- Timer precision hardening ----
  try {
    const _realNow = performance.now.bind(performance);
    Object.defineProperty(performance, 'now', {
      value: function now() {
        return Math.floor(_realNow() * 1) / 1;
      },
      configurable: true, writable: true
    });
  } catch(_) {}

})();
`;

  (() => {
    if (typeof Worker === 'undefined') return;

    const _RealWorker = window.Worker;

    function PatchedWorker(scriptURL, options) {
      let wrappedURL;
      try {
        const preambleBlob = new Blob(
          [`${workerPreamble}\nimportScripts(${JSON.stringify(String(scriptURL))});`],
          { type: 'application/javascript' }
        );
        wrappedURL = URL.createObjectURL(preambleBlob);
      } catch (_) {
        wrappedURL = scriptURL;
      }

      const worker = new _RealWorker(wrappedURL, options);

      if (wrappedURL !== scriptURL) {
        setTimeout(() => { try { URL.revokeObjectURL(wrappedURL); } catch(_) {} }, 5000);
      }
      return worker;
    }

    PatchedWorker.prototype = _RealWorker.prototype;
    Object.setPrototypeOf(PatchedWorker, _RealWorker);

    try {
      Object.defineProperty(PatchedWorker, 'name', { value: 'Worker', configurable: true });
      PatchedWorker.toString = function() { return 'function Worker() { [native code] }'; };
    } catch (_) {}

    defineProp(window, 'Worker', {
      value: PatchedWorker,
      configurable: true,
      writable: true,
    });

    if (typeof SharedWorker !== 'undefined') {
      const _RealSharedWorker = SharedWorker;

      function PatchedSharedWorker(scriptURL, options) {
        let wrappedURL;
        try {
          const preambleBlob = new Blob(
            [`${workerPreamble}\nimportScripts(${JSON.stringify(String(scriptURL))});`],
            { type: 'application/javascript' }
          );
          wrappedURL = URL.createObjectURL(preambleBlob);
        } catch (_) {
          wrappedURL = scriptURL;
        }
        const sw = new _RealSharedWorker(wrappedURL, options);
        if (wrappedURL !== scriptURL) {
          setTimeout(() => { try { URL.revokeObjectURL(wrappedURL); } catch(_) {} }, 5000);
        }
        return sw;
      }

      PatchedSharedWorker.prototype = _RealSharedWorker.prototype;
      Object.setPrototypeOf(PatchedSharedWorker, _RealSharedWorker);
      try {
        Object.defineProperty(PatchedSharedWorker, 'name', { value: 'SharedWorker', configurable: true });
        PatchedSharedWorker.toString = function() { return 'function SharedWorker() { [native code] }'; };
      } catch (_) {}

      defineProp(window, 'SharedWorker', {
        value: PatchedSharedWorker,
        configurable: true,
        writable: true,
      });
    }
  })();

  // =========================================================================
  // 11. Service Worker registration interception (Worker scope consistency)
  // =========================================================================
  (() => {
    if (typeof navigator === 'undefined' || !navigator.serviceWorker) return;

    const _origRegister = navigator.serviceWorker.register;
    defineProp(navigator.serviceWorker, 'register', {
      value: nativify(async function register(scriptURL, options) {
        try {
          const response = await fetch(scriptURL);
          const code = await response.text();
          const blob = new Blob(
            [`${workerPreamble}\n${code}`],
            { type: 'application/javascript' }
          );
          const blobURL = URL.createObjectURL(blob);
          return await _origRegister.call(this, blobURL, options);
        } catch (_) {
          return await _origRegister.call(this, scriptURL, options);
        }
      }, 'register'),
      writable: true, configurable: true
    });
  })();

  // =========================================================================
  // 12. Permissions API consistency
  // =========================================================================
  (() => {
    if (typeof navigator === 'undefined' || !navigator.permissions) return;

    const PERMISSION_MAP = {
      'geolocation':            'denied',
      'notifications':          'denied',
      'push':                   'denied',
      'midi':                   'denied',
      'camera':                 'denied',
      'microphone':             'denied',
      'speaker-selection':      'denied',
      'device-info':            'denied',
      'background-fetch':       'denied',
      'background-sync':        'granted',
      'bluetooth':              'denied',
      'persistent-storage':     'denied',
      'ambient-light-sensor':   'denied',
      'accelerometer':          'denied',
      'gyroscope':              'denied',
      'magnetometer':           'denied',
      'clipboard-read':         'denied',
      'clipboard-write':        'denied',
      'display-capture':        'denied',
      'nfc':                    'denied',
    };

    const _realQuery = navigator.permissions.query.bind(navigator.permissions);

    const patchedQuery = nativify(async function query(descriptor) {
      const name = descriptor && descriptor.name;
      if (name && PERMISSION_MAP.hasOwnProperty(name)) {
        const state = PERMISSION_MAP[name];
        return Object.freeze({
          state,
          name,
          onchange: null,
          addEventListener: nativify(function() {}, 'addEventListener'),
          removeEventListener: nativify(function() {}, 'removeEventListener'),
          dispatchEvent: nativify(function() { return false; }, 'dispatchEvent'),
        });
      }
      try {
        return await _realQuery(descriptor);
      } catch (_) {
        return Object.freeze({ state: 'denied', name, onchange: null });
      }
    }, 'query');

    defineProp(navigator.permissions, 'query', {
      value: patchedQuery,
      configurable: true,
      writable: true,
    });
  })();

  }

  __camou_main_patch();
})();
