/* Sets Mermaid's `look` to handDrawn (Excalidraw-like) for all diagrams.
   Does NOT call mermaid.run()/initialize() itself: it only wraps initialize()
   so that mkdocs-material's own call carries the extra option. */
(function () {
  if (typeof mermaid === "undefined" || !mermaid.initialize) return
  var init = mermaid.initialize.bind(mermaid)
  mermaid.initialize = function (config) {
    return init(Object.assign({}, config, { look: "handDrawn", handDrawnSeed: 1 }))
  }
})()
