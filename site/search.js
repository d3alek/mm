(function () {
  var el = document.getElementById("search");
  var params = new URLSearchParams(location.search);
  var ui = new PagefindUI({
    element: el,
    baseUrl: el.dataset.base,
    showSubResults: true,
    showImages: false,
    excerptLength: 30,
    autofocus: !params.get("q"),
    // Keep ?q= in the address bar so a search can be shared or reloaded.
    processTerm: function (term) {
      var url = new URL(location.href);
      if (term) url.searchParams.set("q", term);
      else url.searchParams.delete("q");
      history.replaceState(null, "", url);
      return term;
    },
  });
  if (params.get("q")) ui.triggerSearch(params.get("q"));
})();
