// ================= AI ホストの 3D アバター(VRM)の描画 =================
// サーバから届く感情(normal/happy/excited/troubled/shy)と VOICEVOX の音声で動かす。
// 読み込みに失敗したら、何もせず元の SVG のイラストを残す(index.html の #avatarBox)。
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils, VRMExpressionPresetName } from '@pixiv/three-vrm';

const MODEL_URL = '/static/models/hana.vrm';

// 感情 → VRM の標準表情の強さ(モデルにない表情は自動で飛ばす)
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
    console.warn('[vrm] WebGL が使えないため、イラストのまま表示します:', e);
    return;
  }

  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(26, 1, 0.1, 20);
  camera.position.set(0, 1.3, 1.45);

  // ライティング:暖色のキーライト(斜め前)+ 弱めの環境光 + 背面からのリムライト(撮影スタジオ風)
  scene.add(new THREE.AmbientLight(0xfffaf3, 1.25));
  const key = new THREE.DirectionalLight(0xfff1e2, 1.35);
  key.position.set(0.8, 1.8, 2.4);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xf3f0ea, 0.5);
  rim.position.set(-1.6, 1.4, -1.6);
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

  // ---- 表情・口の状態 ----
  const cur = {}, tgt = {};           // 表情の今の値 / 目標の値
  EXPR_KEYS.forEach(k => { cur[k] = 0; tgt[k] = 0; });
  let mouth = 0;                       // 口の開き 0..1
  let lipActive = false;              // 実際の音声に合わせて口を動かしている
  let fakeTalkUntil = 0;             // 音声がないとき、口を動かし続ける終了時刻
  let blinkT = 1 + Math.random() * 3, blinking = 0;
  let armL = null, armR = null, foreL = null, foreR = null;  // 腕の骨(T ポーズから腕を下ろすため)

  // ---- 音声から口の動きを解析 ----
  let actx = null, analyser = null, buf = null;
  const connected = new WeakSet();
  function ensureCtx() {
    if (actx) return;
    try {
      actx = new (window.AudioContext || window.webkitAudioContext)();
      analyser = actx.createAnalyser();
      analyser.fftSize = 512;
      buf = new Uint8Array(analyser.fftSize);
    } catch (e) { console.warn('[vrm] AudioContext を作れませんでした:', e); }
  }
  function readMouth() {
    if (!analyser) return 0;
    analyser.getByteTimeDomainData(buf);
    let s = 0;
    for (let i = 0; i < buf.length; i++) { const d = (buf[i] - 128) / 128; s += d * d; }
    return Math.min(1, Math.sqrt(s / buf.length) * 11);
  }

  // ---- 外から使う API(window.HANA) ----
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
      } catch (e) { console.warn('[vrm] 音声に合わせた口の動きに失敗したため、時間で動かします:', e); }
    },
  };

  // ---- モデルの読み込み ----
  const loader = new GLTFLoader();
  loader.register(p => new VRMLoaderPlugin(p));
  loader.load(MODEL_URL, (gltf) => {
    try {
      vrm = gltf.userData.vrm;
      // バージョンによってある最適化。あるときだけ呼ぶ
      if (typeof VRMUtils.removeUnnecessaryVertices === 'function') VRMUtils.removeUnnecessaryVertices(gltf.scene);
      if (typeof VRMUtils.removeUnnecessaryJoints === 'function') VRMUtils.removeUnnecessaryJoints(gltf.scene);
      if (typeof VRMUtils.combineSkeletons === 'function') VRMUtils.combineSkeletons(gltf.scene);
      if (vrm.meta && (vrm.meta.metaVersion === '0' || vrm.meta.metaVersion === 0)) {
        VRMUtils.rotateVRM0(vrm);
      }
      vrm.scene.traverse(o => { o.frustumCulled = false; });
      scene.add(vrm.scene);

      // 腕を下ろす:初期状態は T ポーズなので、自然に下ろした姿勢の骨を取っておく
      const H = vrm.humanoid;
      armL = H.getNormalizedBoneNode('leftUpperArm');
      armR = H.getNormalizedBoneNode('rightUpperArm');
      foreL = H.getNormalizedBoneNode('leftLowerArm');
      foreR = H.getNormalizedBoneNode('rightLowerArm');

      // 头骨位置を基準に「上半身」を構図:頭の上に少し余白を残し(頭頂が切れない)、目線よりわずかに上から撮る
      const head = vrm.humanoid.getNormalizedBoneNode('head');
      const hp = new THREE.Vector3();
      (head || vrm.scene).getWorldPosition(hp);
      const ty = (head ? hp.y : 1.3) - 0.08;
      camera.position.set(0, ty + 0.03, 1.72);
      camera.lookAt(0, ty, 0);

      window.HANA.ready = true;
      stage.classList.add('vrm-on');
      console.log('[vrm] 花奈の準備ができました');
    } catch (e) {
      vrm = null;
      console.warn('[vrm] 初期化に失敗したため、イラストのまま表示します:', e);
    }
  }, undefined, (err) => {
    console.warn('[vrm] モデルを読み込めなかったため、イラストのまま表示します:', err);
  });

  // ---- 描画のループ ----
  const clock = new THREE.Clock();
  function tick() {
    requestAnimationFrame(tick);
    const dt = clock.getDelta();
    const t = clock.elapsedTime;

    if (vrm) {
      // 表情をなめらかに切り替える
      EXPR_KEYS.forEach(k => {
        cur[k] += (tgt[k] - cur[k]) * Math.min(1, dt * 8);
        setExpr(k, cur[k]);
      });

      // 口:実際の音声 → なければ時間で動かす → どちらもなければ閉じる
      let m = 0;
      if (lipActive) m = readMouth();
      else if (performance.now() < fakeTalkUntil) m = (0.55 + 0.45 * Math.sin(t * 17)) * 0.7;
      mouth += (m - mouth) * Math.min(1, dt * 20);
      setExpr(VRMExpressionPresetName.Aa, mouth);

      // まばたき(happy/excited のときは目を細めているので、ぶつからないよう飛ばす)
      blinkT -= dt;
      if (blinkT <= 0 && cur.happy < 0.4) { blinking = 0.001; blinkT = 2.5 + Math.random() * 3.5; }
      if (blinking > 0) {
        blinking += dt;
        const b = blinking < 0.07 ? blinking / 0.07 : Math.max(0, 1 - (blinking - 0.07) / 0.1);
        setExpr(VRMExpressionPresetName.Blink, b);
        if (blinking > 0.17) { blinking = 0; setExpr(VRMExpressionPresetName.Blink, 0); }
      }

      // 腕を下ろす(毎フレーム設定し直す。update に上書きされないように)+ 呼吸に合わせたわずかな動き
      const breathe = Math.sin(t * 1.4) * 0.02;
      if (armL) armL.rotation.z = -1.22 + breathe;
      if (armR) armR.rotation.z = 1.22 - breathe;
      if (foreL) foreL.rotation.z = -0.14;
      if (foreR) foreR.rotation.z = 0.14;

      // 待機中:わずかな揺れと呼吸
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
