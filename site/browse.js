(function () {
  var q = document.getElementById("q");
  var fp = document.getElementById("fp");
  var fs = document.getElementById("fs");
  var fy = document.getElementById("fy");
  var count = document.getElementById("count");
  var rows = Array.prototype.slice.call(document.querySelectorAll("#list li"));
  var params = new URLSearchParams(location.search);
  q.value = params.get("q") || "";
  fp.value = params.get("platform") || "";
  fs.value = params.get("series") || "";
  fy.value = params.get("year") || "";

  function apply() {
    var p = fp.value, s = fs.value, y = fy.value;
    var words = q.value.toLowerCase().split(/\s+/).filter(Boolean);
    Array.prototype.forEach.call(fs.options, function (o) {
      o.hidden = !!(p && o.value && o.dataset.p !== p);
    });
    if (s && fs.selectedOptions[0].hidden) { fs.value = ""; s = ""; }
    var shown = 0;
    rows.forEach(function (li) {
      var text = li.textContent.toLowerCase();
      var ok = (!p || li.dataset.p === p) && (!s || li.dataset.s === s) && (!y || li.dataset.y === y) &&
        words.every(function (w) { return text.indexOf(w) !== -1; });
      li.hidden = !ok;
      if (ok) shown++;
    });
    count.textContent = shown + " of " + rows.length + " talks";
    var url = new URL(location.href);
    [["q", q.value], ["platform", p], ["series", s], ["year", y]].forEach(function (kv) {
      if (kv[1]) url.searchParams.set(kv[0], kv[1]); else url.searchParams.delete(kv[0]);
    });
    history.replaceState(null, "", url);
  }
  [q, fp, fs, fy].forEach(function (e) { e.addEventListener("input", apply); });
  apply();
})();
