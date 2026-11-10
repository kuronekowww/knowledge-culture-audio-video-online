// Replace only this file and the contents inside #scene-stage when authoring.
// Use absolute source-audio seconds. Add unpaused children to the paused root.
// Every property needs a defined initial state and deterministic seek behaviour.
window.KNOWLEDGE_CONTENT = {
  authored: false,
  build(timeline, stage, data) {
    stage.textContent = '待制作：开场动作与正文画面';
  }
};
