// Abre um perfil W2H pelo IE (InternetExplorerMedium) sem pywin32:
//   cscript //NoLogo //E:JScript _ie_open.js <url_indice> <texto_do_link> [timeout_s]
// Imprime UMA linha JSON com o resultado (so ASCII: o resto vai como \uXXXX). Nao digita nada: so navega e clica no
// link do perfil (que abre o popup da sessao). A URL chega por argumento (nenhum endereco fica neste arquivo).
var args = WScript.Arguments;
var url = args(0), label = args(1);
var timeout_s = 60;
if (args.length > 2) {
  var parsed = parseInt(args(2), 10);
  if (!isNaN(parsed) && parsed > 0) timeout_s = parsed;
}
function esc(s) {
  var r = "";
  for (var i = 0; i < s.length; i++) {
    var c = s.charCodeAt(i);
    if (c == 34) r += '\\"';
    else if (c == 92) r += "\\\\";
    else if (c < 32 || c > 126) {
      var h = c.toString(16);
      while (h.length < 4) h = "0" + h;
      r += "\\u" + h;
    } else r += s.charAt(i);
  }
  return r;
}
function out(o) {
  var parts = [];
  for (var k in o) {
    var v = o[k];
    if (typeof v === "string") parts.push('"' + esc(k) + '":"' + esc(v) + '"');
    else if (typeof v === "number" || typeof v === "boolean") parts.push('"' + esc(k) + '":' + v);
    else parts.push('"' + esc(k) + '":"' + esc(String(v)) + '"');
  }
  WScript.StdOut.WriteLine("{" + parts.join(",") + "}");
}
var ie;
try {
  ie = GetObject("new:{D5E8041D-920F-45e9-B8FB-B1DEB82C6E5E}");
} catch (e) {
  out({ ok: false, step: "create", error: String(e.message) });
  WScript.Quit(2);
}
try {
  ie.Visible = false;
  ie.Navigate(url);
  var t = 0, limit = timeout_s * 10;
  while ((ie.Busy || ie.ReadyState != 4) && t < limit) { WScript.Sleep(100); t++; }
  if (t >= limit) { out({ ok: false, step: "load", error: "timeout" }); ie.Quit(); WScript.Quit(3); }
  var links = ie.Document.getElementsByTagName("a"), found = false;
  for (var i = 0; i < links.length; i++) {
    if (links[i].innerText == label) { links[i].click(); found = true; break; }
  }
  var hwnd = ie.HWND;
  WScript.Sleep(1500);
  ie.Quit();
  out({ ok: found, step: found ? "clicked" : "link_not_found", ie_hwnd: hwnd, links: links.length });
  WScript.Quit(found ? 0 : 4);
} catch (e) {
  out({ ok: false, step: "navigate", error: String(e.message) });
  try { ie.Quit(); } catch (e2) {}
  WScript.Quit(5);
}
