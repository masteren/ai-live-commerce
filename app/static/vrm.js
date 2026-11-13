// ================= AI主播 VRM 渲染 =================
// 由后端 emotion(normal/happy/excited/troubled/shy) + VOICEVOX 音频驱动。
// 加载失败会静默保留原 SVG(见 index.html 的 #avatarBox 兜底)。
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils, VRMExpressionPresetName } from '@pixiv/three-vrm';

const MODEL_URL = '/static/models/hana.vrm';

// 情绪 → VRM 标准表情权重(缺失的表情会自动跳过)
const EMOTION_EXPR = {
  normal:   {},
  happy:    { happy: 0.9 },
  excited:  { happy: 1.0, surprised: 0.15 },
  troubled: { sad: 0.85 },
  shy:      { relaxed: 0.6, happy: 0.3 },
};
const EXPR_KEYS = ['happy', 'sad', 'angry', 'relaxed', 'surprised'];

function boot() {
  const canvas = document.getElementById('vrmCanvas');
  const stage = canvas && canvas.closest('.stage');
  if (!canvas || !stage) return;

  let renderer, scene, camera, vrm = null;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  } catch (e) {
    console.warn('[vrm] WebGL 不可用,保留 SVG 兜底:', e);
    return;
  }

  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(26, 1, 0.1, 20);
  camera.position.set(0, 1.3, 1.45);

  scene.add(new THREE.AmbientLight(0xffffff, 1.6));
  const key = new THREE.DirectionalLight(0xffffff, 1.4);
  key.position.set(1, 2, 2.2);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xdfe8f5, 0.6);
  rim.position.set(-1.5, 1.5, -1.5);
  scene.add(rim);

  function resize() {
    const w = stage.clientWidth || 640;
    const h = stage.clientHeight || 400;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(stage);
  resize();

  // ---- 表情 / 口型 状态 ----
  const cur = {}, tgt = {};           // 情绪表情当前值 / 目标值
  EXPR_KEYS.forEach(k => { cur[k] = 0; tgt[k] = 0; });
  let mouth = 0;                       // 当前口型开合 0..1
  let lipActive = false;              // 真·音频口型
  let fakeTalkUntil = 0;             // 无音频时的假口型截止时间
  let blinkT = 1 + Math.random() * 3, blinking = 0;
  let armL = null, armR = null, foreL = null, foreR = null;  // 手臂骨骼(放下 T-pose 用)

  // ---- 音频口型分析 ----
  let actx = null, analyser = null, buf = null;
  const connected = new WeakSet();
  function ensureCtx() {
    if (actx) return;
    try {
      actx = new (window.AudioContext || window.webkitAudioContext)();
      analyser = actx.createAnalyser();
      analyser.fftSize = 512;
      buf = new Uint8Array(analyser.fftSize);
    } catch (e) { console.warn('[vrm] AudioContext 失败:', e); }
  }
  function readMouth() {
    if (!analyser) return 0;
    analyser.getByteTimeDomainData(buf);
    let s = 0;
    for (let i = 0; i < buf.length; i++) { const d = (buf[i] - 128) / 128; s += d * d; }
    return Math.min(1, Math.sqrt(s / buf.length) * 11);
  }

  // ---- 对外 API ----
  const setExpr = (name, w) => {
    if (vrm && vrm.expressionManager && vrm.expressionManager.getExpression(name)) {
      vrm.expressionManager.setValue(name, w);
    }
  };
  window.HANA = {
    ready: false,
    setEmotion(emo) {
      EXPR_KEYS.forEach(k => { tgt[k] = 0; });
      const map = EMOTION_EXPR[emo] || EMOTION_EXPR.normal;
      for (const k in map) tgt[k] = map[k];
    },
    talkFor(ms) { fakeTalkUntil = performance.now() + Math.min(ms || 1500, 12000); },
    resumeAudio() { ensureCtx(); if (actx && actx.state === 'suspended') actx.resume(); },
    attachAudio(el) {
      ensureCtx();
      if (!actx || !analyser) return;
      try {
        if (!connected.has(el)) {
          const src = actx.createMediaElementSource(el);
          src.connect(analyser);
          analyser.connect(actx.destination);
          connected.add(el);
        }
        lipActive = true;
        el.addEventListener('ended', () => { lipActive = false; }, { once: true });
        el.addEventListener('pause', () => { lipActive = false; }, { once: true });
      } catch (e) { console.warn('[vrm] lipsync 失败,退回时长口型:', e); }
    },
  };

  // ---- 加载模型 ----
  const loader = new GLTFLoader();
  loader.register(p => new VRMLoaderPlugin(p));
  loader.load(MODEL_URL, (gltf) => {
    try {
      vrm = gltf.userData.vrm;
      // 版本间可选优化,存在才调用
      if (typeof VRMUtils.removeUnnecessaryVertices === 'function') VRMUtils.removeUnnecessaryVertices(gltf.scene);
      if (typeof VRMUtils.removeUnnecessaryJoints === 'function') VRMUtils.removeUnnecessaryJoints(gltf.scene);
      if (typeof VRMUtils.combineSkeletons === 'function') VRMUtils.combineSkeletons(gltf.scene);
      if (vrm.meta && (vrm.meta.metaVersion === '0' || vrm.meta.metaVersion === 0)) {
        VRMUtils.rotateVRM0(vrm);
      }
      vrm.scene.traverse(o => { o.frustumCulled = false; });
      scene.add(vrm.scene);

      // 放下双臂:默认是 T-pose,取自然垂放姿势的骨骼
      const H = vrm.humanoid;
      armL = H.getNormalizedBoneNode('leftUpperArm');
      armR = H.getNormalizedBoneNode('rightUpperArm');
      foreL = H.getNormalizedBoneNode('leftLowerArm');
      foreR = H.getNormalizedBoneNode('rightLowerArm');

      // 依据头骨位置取景为「上半身胸像」
      const head = vrm.humanoid.getNormalizedBoneNode('head');
      const hp = new THREE.Vector3();
      (head || vrm.scene).getWorldPosition(hp);
      const ty = (head ? hp.y : 1.3) - 0.12;
      camera.position.set(0, ty, 1.42);
      camera.lookAt(0, ty, 0);

      window.HANA.ready = true;
      stage.classList.add('vrm-on');
      console.log('[vrm] 花奈 已就绪');
    } catch (e) {
      vrm = null;
      console.warn('[vrm] 初始化失败,保留 SVG 兜底:', e);
    }
  }, undefined, (err) => {
    console.warn('[vrm] 模型加载失败,保留 SVG 兜底:', err);
  });

  // ---- 渲染循环 ----
  const clock = new THREE.Clock();
  function tick() {
    requestAnimationFrame(tick);
    const dt = clock.getDelta();
    const t = clock.elapsedTime;

    if (vrm) {
      // 情绪表情平滑过渡
      EXPR_KEYS.forEach(k => {
        cur[k] += (tgt[k] - cur[k]) * Math.min(1, dt * 8);
        setExpr(k, cur[k]);
      });

      // 口型:真音频优先,否则时长假口型,否则闭合
      let m = 0;
      if (lipActive) m = readMouth();
      else if (performance.now() < fakeTalkUntil) m = (0.55 + 0.45 * Math.sin(t * 17)) * 0.7;
      mouth += (m - mouth) * Math.min(1, dt * 20);
      setExpr(VRMExpressionPresetName.Aa, mouth);

      // 眨眼(happy/excited 时眼睛已眯,跳过以免打架)
      blinkT -= dt;
      if (blinkT <= 0 && cur.happy < 0.4) { blinking = 0.001; blinkT = 2.5 + Math.random() * 3.5; }
      if (blinking > 0) {
        blinking += dt;
        const b = blinking < 0.07 ? blinking / 0.07 : Math.max(0, 1 - (blinking - 0.07) / 0.1);
        setExpr(VRMExpressionPresetName.Blink, b);
        if (blinking > 0.17) { blinking = 0; setExpr(VRMExpressionPresetName.Blink, 0); }
      }

      // 放下手臂(每帧重设,防止 update 覆盖) + 轻微呼吸摆动
      const breathe = Math.sin(t * 1.4) * 0.02;
      if (armL) armL.rotation.z = -1.22 + breathe;
      if (armR) armR.rotation.z = 1.22 - breathe;
      if (foreL) foreL.rotation.z = -0.14;
      if (foreR) foreR.rotation.z = 0.14;

      // 待机:轻微摇摆 + 呼吸
      vrm.scene.rotation.y = Math.sin(t * 0.7) * 0.03;
      vrm.scene.position.y = Math.sin(t * 1.6) * 0.004;

      vrm.update(dt);
    }
    renderer.render(scene, camera);
  }
  tick();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
