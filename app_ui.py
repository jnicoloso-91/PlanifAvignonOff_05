##################
# application UI #
##################

import streamlit as st
import pandas as pd
import datetime
import pandas.api.types as ptypes
from st_aggrid import AgGrid, DataReturnMode, GridOptionsBuilder, JsCode, GridUpdateMode
import copy

from app_const import *
from app_utils import *
from app_metier import *
import tracer
import sql_api as sql 
import gsheet_api as gs
import sync_worker as wk
import undo

###########
# JsCodes #
###########

# JsCode chargé de gérer la sélection/déselection programmée de lignes dans les AgGrid, 
# le flip-flop entre grilles "activites_programmees" et "activites_non_programmees" via __sel_source
# et le renvoie correct des modifications de cellules prises en compte par le DOM via response["data"].
# Il exploite les colonnes de travail suivantes:
# __sel_id = id de la ligne à sélectionner (None si pas de contrainte de sélection).
# __sel_ver = version de la demande de sélection (doit être incrémentée à chaque demande).
# __desel_id = id de la ligne devant rester visible lors de la déselection (None si aucune contrainte de visibilité lors de la désélection).
# __desel_ver = version de la demande de désélection (doit être incrémentée à chaque demande).
# __sel_source = information renvoyée par le DOM (event.source exposé par onRowSelected) indiquant si la source de selection est "user" ou "api" selon que la demande de sélection provient d'un click utilisateur ou d'une requête python via JsCode.
# __df_push_ver = permet au JsCode de déclencher un selectionChanged lorsqu'il détecte une incrémentation de la première ligne sur cette colonne, ce qui permet à Streamlit de renvoyer la modification via response["data"], sans attendre de clic utilisateur. 
# Ces colonnes sont configurées par les fonctions utilisateur demander_selection(), demander_deselection() et signaler_df_push()
# L'information de retour __sel_source est exploitée par le mecanisme de flip flop entre grilles "activites_programmees" et "activites_non_programmees" via le response["data"] de l'aggrid,
# ceci afin de ne déclencher le changement d'activité sélectionnée que sur clic user (cf. fonction afficher_activites_programmees() et afficher_activites_non_programmees()).
# Ce JsCode doit être branché sur le onGridReady (voir les grid_options configurées avec les fonctions init_grid_options_xxx).
JS_SELECT_DESELECT_ONCE = JsCode(r"""
function(p){
  var api=p&&p.api; if(!api) return;

  // --- identifiant d'instance par IFRAME, pour éviter tout chevauchement entre grilles ---
  var fe = window.frameElement || null;
  var instId = (function(){
    if (!fe) return 'grid-' + Math.random().toString(36).slice(2);
    var v = fe.getAttribute('data-ag-inst');
    if (!v) { v = 'grid-' + Date.now().toString(36).slice(2) + '-' + Math.random().toString(36).slice(2);
              fe.setAttribute('data-ag-inst', v); }
    return v;
  })();

  // --- caches par instance (désélection / sélection) ---
  window.__agSelCache   = window.__agSelCache   || {};
  window.__agDeselCache = window.__agDeselCache || {};
  var selCache   = window.__agSelCache;
  var deselCache = window.__agDeselCache;

  // ======================= Helpers DataFrame/meta =======================
  function firstRow(){
    var n=api.getDisplayedRowCount?api.getDisplayedRowCount():0;
    return (n>0) ? api.getDisplayedRowAtIndex(0) : null;
  }

  function updateMetaIfChanged(key, val){
    try{
      var r0=firstRow(); if(!r0||!r0.data) return;
      if(r0.data[key] === val) return;
      var row=Object.assign({}, r0.data);
      row[key]=val;
      api.applyTransaction && api.applyTransaction({ update:[row] });
    }catch(e){}
  }

  function scan(col){
    var n=api.getDisplayedRowCount?api.getDisplayedRowCount():0;
    for(var i=0;i<n;i++){
      var r=api.getDisplayedRowAtIndex(i);
      if(r&&r.data&&r.data[col]!=null) return String(r.data[col]);
    }
    return null;
  }

  function readMeta(){
    return {
      deselVer: scan("__desel_ver"),
      deselId:  scan("__desel_id"),
      selId:    scan("__sel_id"),
      selVer:   scan("__sel_ver")
    };
  }

  // ======================= DF push nudge =======================
  // On déclenche un "nudge" (évènement selectionChanged API) quand __df_push_ver change,
  // pour forcer st_aggrid à renvoyer response["data"] à Python.
  var lastDfVer = null;
  function dfPushVer(){ return scan("__df_push_ver"); }

  function nudgeReturn(){
    try{
      // Marque comme API pour ne pas perturber ta logique flip-flop
      updateMetaIfChanged("__sel_source","api");

      var selected = api.getSelectedNodes ? api.getSelectedNodes() : null;
      if (selected && selected.length){
        var n = selected[0];
        n.setSelected(false, true, true);
        n.setSelected(true,  true, true);
      } else {
        var first = api.getDisplayedRowAtIndex && api.getDisplayedRowAtIndex(0);
        if(first){
          first.setSelected(true,  true, true);
          first.setSelected(false, true, true);
        } else {
          api.dispatchEvent && api.dispatchEvent({type:"selectionChanged"});
        }
      }
    }catch(e){}
  }

  function nudgeIfDfPushed(){
    var v = dfPushVer();
    if (v!=null && v!==lastDfVer){
      lastDfVer = v;
      nudgeReturn();
    }
  }

  // ======================= recherche de node + scroll =======================
  function findNodeByUuid(id){
    if(id==null) return null;
    var node=api.getRowNode?api.getRowNode(String(id)):null;
    if(node) return node;
    var n=api.getDisplayedRowCount?api.getDisplayedRowCount():0;
    for(var i=0;i<n;i++){
      var r=api.getDisplayedRowAtIndex(i);
      if(r&&r.data&&String(r.data.__uuid)===String(id)) return r;
    }
    return null;
  }

  function ensureVisible(node){
    if(!node) return;
    if(typeof node.rowIndex==="number" && api.ensureIndexVisible){
      api.ensureIndexVisible(node.rowIndex,"middle");
    } else if(api.ensureNodeVisible){
      api.ensureNodeVisible(node,"middle");
    }
  }

  // ======================= scheduler léger =======================
  var schedPending=false;
  function sched(){ if(schedPending) return; schedPending=true; setTimeout(function(){schedPending=false; run();},30); }

  // ======================= coeur (dé)sélection : priorité à la désélection =======================
  function run(){
    nudgeIfDfPushed();  // vérifier les pushes DF à chaque passage
    var m=readMeta(); if(!m) return;

    // 1) déselection programmée
    if(m.deselVer!=null && deselCache[instId]!==m.deselVer){
      updateMetaIfChanged("__sel_source","api");
      api.deselectAll && api.deselectAll();
      ensureVisible(findNodeByUuid(m.deselId));
      deselCache[instId]=m.deselVer;
    }

    // 2) sélection programmée (once)
    if(m.selId!=null && m.selVer!=null && selCache[instId]!==m.selVer){
      updateMetaIfChanged("__sel_source","api");
      var node=findNodeByUuid(m.selId);
      if(node){
        api.deselectAll && api.deselectAll();
        node.setSelected && node.setSelected(true,true,true); // source=api
        ensureVisible(node);
      }
      selCache[instId]=m.selVer;
    }
  }

  // ======================= marquer la source user/api =======================
  function onRowSelected(ev){
    var src=(ev && ev.source) ? String(ev.source) : "api";
    var human=(src==="rowClicked" || src==="checkboxSelected" || src==="touch" || src==="selectAll") ? "user" : "api";
    updateMetaIfChanged("__sel_source", human);
  }

  // ======================= wiring AG Grid =======================
  if(p.type==="gridReady"){
    delete deselCache[instId];
    delete selCache[instId];

    api.addEventListener && api.addEventListener('rowSelected', onRowSelected);

    ["firstDataRendered","modelUpdated","rowDataUpdated"].forEach(function(e){
      api.addEventListener && api.addEventListener(e, function(){
        nudgeIfDfPushed();  // ← déclenche le nudge si push détecté
        sched();
      });
    });

    setTimeout(function(){
      // init
      updateMetaIfChanged("__sel_source","api"); // état neutre au boot
      nudgeIfDfPushed();                         // ← check tout de suite à l'init
      sched();
    }, 0);
  } else {
    nudgeIfDfPushed();
    sched();
  }
}
""")

# JS Code chargé de lancer la recherche Web sur la colonne Activité via l'icône loupe
JS_ACTIVITE_ICON_RENDERER = JsCode("""
class ActiviteRenderer {
  init(params){
    const e = document.createElement('div');
    e.style.display='flex'; e.style.alignItems='center'; e.style.gap='0.4rem';
    e.style.width='100%'; e.style.overflow='hidden';

    const label = (params.value ?? '').toString();
    const raw = params.data ? (params.data['Hyperlien'] ?? params.data['Hyperliens'] ?? '') : '';
    const href = String(raw || ("https://www.festivaloffavignon.com/resultats-recherche?recherche="+encodeURIComponent(label))).trim();

    const txt = document.createElement('span');
    txt.style.flex='1 1 auto'; txt.style.overflow='hidden'; txt.style.textOverflow='ellipsis';
    txt.textContent = label;
    // 🔸 pas de handler dblclick ici → AG Grid capte tout seul le double-clic
    e.appendChild(txt);

    const a = document.createElement('a');
    a.textContent = '🔎';
    a.href = href;
    a.target = '_blank';
    a.rel = 'noopener,noreferrer';
    a.title = 'Rechercher / Ouvrir le lien';
    a.style.flex='0 0 auto'; a.style.textDecoration='none'; a.style.userSelect='none';
    // on bloque juste la propagation pour ne pas déclencher sélection/édition
    a.addEventListener('click', ev => {
        ev.stopPropagation();
        openPreferNewTab(href);
    });
    e.appendChild(a);

    this.eGui = e;
  }
  getGui(){ return this.eGui; }
  refresh(){ return false; }
}
""")

# JS Code chargé de lancer la recherche d'itinéraire sur la colonne Lieu via l'icône épingle
JS_LIEU_ICON_RENDERER = JsCode("""
class LieuRenderer {
  init(params){
    const e = document.createElement('div');
    e.style.display='flex'; e.style.alignItems='center'; e.style.gap='0.4rem';
    e.style.width='100%'; e.style.overflow='hidden';

    const label = (params.value ?? '').toString().trim();

    // ---- adresse résolue (si dispo) ----
    const addrEnc = (params.data && params.data.__addr_enc)
      ? String(params.data.__addr_enc).trim()
      : encodeURIComponent(label || "");

    // ---- préférences + plateforme (depuis gridOptions.context) ----
    const ctx  = params.context || {};
    const app  = ctx.itineraire_app || "Google Maps";
    const plat = ctx.platform || (
      /iPad|iPhone|iPod/.test(navigator.userAgent) ? "iOS"
      : /Android/.test(navigator.userAgent) ? "Android" : "Desktop"
    );

    // ---- construire l'URL comme ton bouton ----
    let url = "#";
    if (addrEnc) {
      if (app === "Apple Maps" && plat === "iOS") {
        url = `http://maps.apple.com/?daddr=${addrEnc}`;
      } else if (app === "Google Maps") {
        if (plat === "iOS")       url = `comgooglemaps://?daddr=${addrEnc}`;
        else if (plat === "Android") url = `geo:0,0?q=${addrEnc}`;
        else                      url = `https://www.google.com/maps/dir/?api=1&destination=${addrEnc}`;
      } else {
        url = `https://www.google.com/maps/dir/?api=1&destination=${addrEnc}`;
      }
    }

    // ---- texte cellule (double-clic géré nativement par AG Grid) ----
    const txt = document.createElement('span');
    txt.style.flex='1 1 auto'; txt.style.overflow='hidden'; txt.style.textOverflow='ellipsis';
    txt.textContent = label;
    e.appendChild(txt);

    // ---- icône itinéraire (épingle) ----
    const a = document.createElement('a');
    a.textContent = '📍';
    a.href = url;
    a.target = (url === '#') ? '_self' : '_blank';
    a.rel = 'noopener,noreferrer';
    a.title = 'Itinéraire vers ce lieu';
    a.style.flex='0 0 auto'; a.style.textDecoration='none'; a.style.userSelect='none';
    if (url === '#') { a.style.opacity = 0.4; a.style.pointerEvents = 'none'; }
    a.addEventListener('click', ev => {
        ev.stopPropagation();
        openPreferNewTab(href);
    });
    e.appendChild(a);

    this.eGui = e;
  }
  getGui(){ return this.eGui; }
  refresh(){ return false; }
}
""")

# JS Code chargé de lancer la recherche Web sur la colonne Activité via appui long (mais figeage d'interface sur IOS au retour de la page Web)
JS_ACTIVITE_LONGPRESS_RENDERER = JsCode("""
class ActiviteRenderer {
  init(params){
    // ---- conteneur + texte ----
    var e = document.createElement('div');
    e.style.display='flex'; e.style.alignItems='center'; e.style.gap='0.4rem';
    e.style.width='100%'; e.style.overflow='hidden';

    var label = (params.value != null ? String(params.value) : '');
    var raw   = (params.data && (params.data['Hyperlien'] || params.data['Hyperliens'])) ? (params.data['Hyperlien'] || params.data['Hyperliens']) : '';
    var href  = String(raw || ("https://www.festivaloffavignon.com/resultats-recherche?recherche="+encodeURIComponent(label))).trim();

    var txt = document.createElement('span');
    txt.style.flex='1 1 auto'; txt.style.overflow='hidden'; txt.style.textOverflow='ellipsis';
    txt.style.cursor='pointer';
    txt.textContent = label;
    e.appendChild(txt);

    function openPreferNewTab(u){
    if (!u) return;
    const ua = navigator.userAgent || "";
    const isIOS =
        /iPad|iPhone|iPod/.test(ua) ||
        (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) ||
        (ua.includes("Mac") && "ontouchend" in window);
    if (isIOS) {
        try {
        // iOS : ouvre un onglet “about:blank” puis redirige (contourne WebKit)
        var w = window.open('about:blank','_blank');
        if (w) { w.location.href = u; return; }
        } catch(_) {}
    }
    // Autres plateformes : nouvel onglet standard
    try { window.open(u, '_blank', 'noopener'); }
    catch(_) { window.location.assign(u); }
    }                                        

    // ---- helper: simuler un vrai clic cellule AG Grid (sélection propre) ----
    function tapSelectViaSyntheticClick(el){
      var cell = el.closest ? el.closest('.ag-cell') : null;
      if (!cell) return;
      try {
        cell.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
        cell.dispatchEvent(new MouseEvent('mouseup',   {bubbles:true}));
        cell.dispatchEvent(new MouseEvent('click',     {bubbles:true}));
      } catch(_){}
    }

    // ---- fallback long-press (si window.attachLongPress absent) ----
    var attachLongPress = (typeof window !== 'undefined' && window.attachLongPress) || function(el, opts){
      var DELAY  = (opts && opts.delay)  != null ? opts.delay  : 550;
      var THRESH = (opts && opts.thresh) != null ? opts.thresh : 8;
      var TAP_MS = (opts && opts.tapMs)  != null ? opts.tapMs  : 220;
      var onUrl  = opts && opts.onUrl;
      var onTap  = opts && opts.onTap;
      const ua = navigator.userAgent || "";
      const isIOS =
        /iPad|iPhone|iPod/.test(ua) ||
        (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) ||
        (ua.includes("Mac") && "ontouchend" in window);

      var sx=0, sy=0, moved=false, pressed=false, timer=null, startT=0, firedLong=false;
      var hadTouchTs = 0;

      function clearT(){ if (timer){ clearTimeout(timer); timer=null; } }
      function now(){ return Date.now(); }
      function withinTouchGrace(){ return (now() - hadTouchTs) < 800; }

      function openSameTab(u){
        if (!u) return;
        try { window.top.location.assign(u); } catch(e){ window.location.assign(u); }
      }
                                     
      function openNewTab(u){
        if (!u) return;
        try {
          var a=document.createElement('a');
          a.href=u; a.target='_blank'; a.rel='noopener,noreferrer';
          a.style.position='absolute'; a.style.left='-9999px'; a.style.top='-9999px';
          document.body.appendChild(a); a.click(); a.remove(); return;
        } catch(e){}
        try { var w=window.open(u,'_blank','noopener'); if (w) return; } catch(e){}
        try { window.location.assign(u); } catch(e){}
      }

      function onDown(ev){
        if (ev.type === 'mousedown' && withinTouchGrace()) return;
        var t = ev.touches ? ev.touches[0] : ev;
        sx = (t && t.clientX) || 0; sy = (t && t.clientY) || 0;
        moved=false; pressed=true; firedLong=false; startT=now();

        clearT();
        timer = setTimeout(function(){
          if (pressed && !moved){
            firedLong = true;
            var u = onUrl ? onUrl() : null;
            if (isIOS) openSameTab(u); else openNewTab(u);
            pressed=false;
          }
        }, DELAY);
      }

      function onMove(ev){
        if (!pressed) return;
        var t = ev.touches ? ev.touches[0] : ev;
        var dx = Math.abs(((t && t.clientX) || 0) - sx);
        var dy = Math.abs(((t && t.clientY) || 0) - sy);
        if (dx>THRESH || dy>THRESH){ moved=true; clearT(); }
      }

      function onUp(ev){
        if (ev.type === 'mouseup' && withinTouchGrace()) return;
        if (!pressed){ clearT(); return; }
        var dur = now() - startT;
        var isTap = (dur < TAP_MS) && !moved;
        pressed=false; clearT();

        if (isTap && !firedLong){
          if (typeof onTap === 'function'){
            // sélection via clic synthétique (pas de sélection "programmée")
            requestAnimationFrame(function(){ try { onTap(); } catch(_){ } });
          }
        }
      }

      function onCancel(){ pressed=false; clearT(); }

      if (window.PointerEvent){
        el.addEventListener('pointerdown', onDown, true);
        el.addEventListener('pointermove', onMove,  true);
        el.addEventListener('pointerup',   onUp,    true);
        el.addEventListener('pointercancel', onCancel, true);
      } else {
        el.addEventListener('touchstart', function(e){ hadTouchTs = now(); onDown(e); }, true);
        el.addEventListener('touchmove',  onMove, true);
        el.addEventListener('touchend',   onUp,   false);
        el.addEventListener('touchcancel', onCancel, true);
        el.addEventListener('mousedown', onDown, true);
        el.addEventListener('mousemove', onMove, true);
        el.addEventListener('mouseup',   onUp,   true);
      }

      el.addEventListener('contextmenu', function(e){ e.preventDefault(); }, true);
      el.style.webkitTouchCallout='none';
      el.style.webkitUserSelect='none';
      el.style.userSelect='none';
      el.style.touchAction='manipulation';
    };

    // ---- branchement unique ----
    attachLongPress(txt, {
      delay: 550,
      thresh: 8,
      tapMs: 220,
      onUrl: function(){ return href; },
      onTap: function(){ tapSelectViaSyntheticClick(txt); }
    });

    this.eGui = e;
  }
  getGui(){ return this.eGui; }
  refresh(){ return false; }
}
""")

# JS Code chargé de lancer la recherche d'itinéraire sur la colonne Lieu via appui long (mais figeage d'interface sur IOS au retour de la page Web)
JS_LIEU_LONGPRESS_RENDERER = JsCode("""
class LieuRenderer {
  init(params){
    // ---- conteneur + texte ----
    var e = document.createElement('div');
    e.style.display='flex'; e.style.alignItems='center'; e.style.gap='0.4rem';
    e.style.width='100%'; e.style.overflow='hidden';

    var label = (params.value != null ? String(params.value) : '').trim();

    var addrEnc = (params.data && params.data.__addr_enc != null)
      ? String(params.data.__addr_enc).trim()
      : encodeURIComponent(label || "");

    var ctx  = params.context || {};
    var app  = ctx.itineraire_app || "Google Maps";
    var plat = ctx.platform || (
      /iPad|iPhone|iPod/.test(navigator.userAgent) ? "iOS"
      : (/Android/.test(navigator.userAgent) ? "Android" : "Desktop")
    );

    var url = "#";
    if (addrEnc) {
      if (app === "Apple Maps" && plat === "iOS") {
        url = "http://maps.apple.com/?daddr=" + addrEnc;
      } else if (app === "Google Maps") {
        if (plat === "iOS")          url = "https://www.google.com/maps/dir/?api=1&destination=" + addrEnc;
        else if (plat === "Android") url = "geo:0,0?q=" + addrEnc;
        else                         url = "https://www.google.com/maps/dir/?api=1&destination=" + addrEnc;
      } else {
        url = "https://www.google.com/maps/dir/?api=1&destination=" + addrEnc;
      }
    }

    var txt = document.createElement('span');
    txt.style.flex='1 1 auto'; txt.style.overflow='hidden'; txt.style.textOverflow='ellipsis';
    txt.style.cursor='pointer';
    txt.textContent = label;
    e.appendChild(txt);

    function openPreferNewTab(u){
    if (!u) return;
    const ua = navigator.userAgent || "";
    const isIOS =
        /iPad|iPhone|iPod/.test(ua) ||
        (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) ||
        (ua.includes("Mac") && "ontouchend" in window);
    if (isIOS) {
        try {
        // iOS : ouvre un onglet “about:blank” puis redirige (contourne WebKit)
        var w = window.open('about:blank','_blank');
        if (w) { w.location.href = u; return; }
        } catch(_) {}
    }
    // Autres plateformes : nouvel onglet standard
    try { window.open(u, '_blank', 'noopener'); }
    catch(_) { window.location.assign(u); }
    }                                    
                                    
    // ---- helper: clic synthétique cellule ----
    function tapSelectViaSyntheticClick(el){
      var cell = el.closest ? el.closest('.ag-cell') : null;
      if (!cell) return;
      try {
        cell.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
        cell.dispatchEvent(new MouseEvent('mouseup',   {bubbles:true}));
        cell.dispatchEvent(new MouseEvent('click',     {bubbles:true}));
      } catch(_){}
    }

    // ---- fallback long-press ----
    var attachLongPress = (typeof window !== 'undefined' && window.attachLongPress) || function(el, opts){
      var DELAY  = (opts && opts.delay)  != null ? opts.delay  : 550;
      var THRESH = (opts && opts.thresh) != null ? opts.thresh : 8;
      var TAP_MS = (opts && opts.tapMs)  != null ? opts.tapMs  : 220;
      var onUrl  = opts && opts.onUrl;
      var onTap  = opts && opts.onTap;
      const ua = navigator.userAgent || "";
      const isIOS =
        /iPad|iPhone|iPod/.test(ua) ||
        (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) ||
        (ua.includes("Mac") && "ontouchend" in window);

      var sx=0, sy=0, moved=false, pressed=false, timer=null, startT=0, firedLong=false;
      var hadTouchTs = 0;

      function clearT(){ if (timer){ clearTimeout(timer); timer=null; } }
      function now(){ return Date.now(); }
      function withinTouchGrace(){ return (now() - hadTouchTs) < 800; }

      function openSameTab(u){
        if (!u) return;
        try { window.top.location.assign(u); } catch(e){ window.location.assign(u); }
      }
                                 
      function openNewTab(u){
        if (!u) return;
        try {
          var a=document.createElement('a');
          a.href=u; a.target='_blank'; a.rel='noopener,noreferrer';
          a.style.position='absolute'; a.style.left='-9999px'; a.style.top='-9999px';
          document.body.appendChild(a); a.click(); a.remove(); return;
        } catch(e){}
        try { var w=window.open(u,'_blank','noopener'); if (w) return; } catch(e){}
        try { window.location.assign(u); } catch(e){}
      }

      function onDown(ev){
        if (ev.type === 'mousedown' && withinTouchGrace()) return;
        var t = ev.touches ? ev.touches[0] : ev;
        sx = (t && t.clientX) || 0; sy = (t && t.clientY) || 0;
        moved=false; pressed=true; firedLong=false; startT=now();

        clearT();
        timer = setTimeout(function(){
          if (pressed && !moved){
            firedLong = true;
            var u = onUrl ? onUrl() : null;
            if (isIOS) openSameTab(u); else openNewTab(u);
            pressed=false;
          }
        }, DELAY);
      }

      function onMove(ev){
        if (!pressed) return;
        var t = ev.touches ? ev.touches[0] : ev;
        var dx = Math.abs(((t && t.clientX) || 0) - sx);
        var dy = Math.abs(((t && t.clientY) || 0) - sy);
        if (dx>THRESH || dy>THRESH){ moved=true; clearT(); }
      }

      function onUp(ev){
        if (ev.type === 'mouseup' && withinTouchGrace()) return;
        if (!pressed){ clearT(); return; }
        var dur = now() - startT;
        var isTap = (dur < TAP_MS) && !moved;
        pressed=false; clearT();

        if (isTap && !firedLong){
          if (typeof onTap === 'function'){
            requestAnimationFrame(function(){ try { onTap(); } catch(_){ } });
          }
        }
      }

      function onCancel(){ pressed=false; clearT(); }

      if (window.PointerEvent){
        el.addEventListener('pointerdown', onDown, true);
        el.addEventListener('pointermove', onMove,  true);
        el.addEventListener('pointerup',   onUp,    true);
        el.addEventListener('pointercancel', onCancel, true);
      } else {
        el.addEventListener('touchstart', function(e){ hadTouchTs = now(); onDown(e); }, true);
        el.addEventListener('touchmove',  onMove, true);
        el.addEventListener('touchend',   onUp,   false);
        el.addEventListener('touchcancel', onCancel, true);
        el.addEventListener('mousedown', onDown, true);
        el.addEventListener('mousemove', onMove, true);
        el.addEventListener('mouseup',   onUp,   true);
      }

      el.addEventListener('contextmenu', function(e){ e.preventDefault(); }, true);
      el.style.webkitTouchCallout='none';
      el.style.webkitUserSelect='none';
      el.style.userSelect='none';
      el.style.touchAction='manipulation';
    };

    // ---- branchement unique ----
    attachLongPress(txt, {
      delay: 550,
      thresh: 8,
      tapMs: 220,
      onUrl: function(){ return url; },
      onTap: function(){ tapSelectViaSyntheticClick(txt); }
    });

    this.eGui = e;
  }
  getGui(){ return this.eGui; }
  refresh(){ return false; }
}
""")

# JS Code chargé de lancer la recherche Web sur la colonne Activité via icône sur IOS et appui long sur autres plateformes
JS_ACTIVITE_RENDERER = JsCode("""
class ActiviteRenderer {
  init(params){
    // helpers
    const ua = navigator.userAgent || "";
    const isIOS =
        /iPad|iPhone|iPod/.test(ua) ||
        (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) ||
        (ua.includes("Mac") && "ontouchend" in window);
    function tapSelect(el){
      const cell = el.closest ? el.closest('.ag-cell') : null;
      if (!cell) return;
      try{
        cell.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
        cell.dispatchEvent(new MouseEvent('mouseup',   {bubbles:true}));
        cell.dispatchEvent(new MouseEvent('click',     {bubbles:true}));
      }catch(_){}
    }
    function openPreferNewTab(u){
      if (!u) return;
      if (isIOS){
        try{ const w = window.open('about:blank','_blank'); if (w){ w.location.href = u; return; } }catch(_){}
      }
      try{ window.open(u,'_blank','noopener'); }catch(_){ window.location.assign(u); }
    }
    function attachLongPress(el, getUrl, onTap){
      const DELAY=550, THRESH=8, TAP_MS=220;
      let sx=0, sy=0, moved=false, pressed=false, startT=0, timer=null, firedLong=false, hadTouchTs=0;
      const now=()=>Date.now(), withinTouchGrace=()=> (now()-hadTouchTs)<800, clearT=()=>{ if(timer){clearTimeout(timer);timer=null;} };
      const onDown=ev=>{ if(ev.type==='mousedown' && withinTouchGrace()) return; const t=ev.touches?ev.touches[0]:ev; sx=t?.clientX||0; sy=t?.clientY||0; moved=false; pressed=true; firedLong=false; startT=now(); clearT(); timer=setTimeout(()=>{ if(pressed&&!moved) firedLong=true; }, DELAY); };
      const onMove=ev=>{ if(!pressed) return; const t=ev.touches?ev.touches[0]:ev; const dx=Math.abs((t?.clientX||0)-sx), dy=Math.abs((t?.clientY||0)-sy); if(dx>THRESH||dy>THRESH){ moved=true; clearT(); } };
      const onUp=ev=>{ if(ev.type==='mouseup' && withinTouchGrace()) return; const dur=now()-startT, isTap=(dur<TAP_MS)&&!moved; pressed=false; clearT(); if(firedLong && !moved){ openPreferNewTab(getUrl()); return; } if(isTap && typeof onTap==='function'){ requestAnimationFrame(onTap); } };
      const onCancel=()=>{ pressed=false; clearT(); };
      if (window.PointerEvent){ el.addEventListener('pointerdown',onDown,{passive:true}); el.addEventListener('pointermove',onMove,{passive:true}); el.addEventListener('pointerup',onUp,{passive:false}); el.addEventListener('pointercancel',onCancel,{passive:true}); }
      else { el.addEventListener('touchstart',e=>{hadTouchTs=now();onDown(e);},{passive:true}); el.addEventListener('touchmove',onMove,{passive:true}); el.addEventListener('touchend',onUp,{passive:false}); el.addEventListener('touchcancel',onCancel,{passive:true}); el.addEventListener('mousedown',onDown,true); el.addEventListener('mousemove',onMove,true); el.addEventListener('mouseup',onUp,false); }
      el.addEventListener('contextmenu', e=>e.preventDefault(), true);
      el.style.webkitTouchCallout='none'; el.style.webkitUserSelect='none'; el.style.userSelect='none'; el.style.touchAction='manipulation';
    }

    // container + label
    const e = document.createElement('div');
    e.style.display='flex'; e.style.alignItems='center'; e.style.gap='0.4rem';
    e.style.width='100%'; e.style.overflow='hidden';

    const label = (params.value != null ? String(params.value) : '');
    const raw   = (params.data && (params.data['Hyperlien'] || params.data['Hyperliens'])) ? (params.data['Hyperlien'] || params.data['Hyperliens']) : '';
    const href  = String(raw || ("https://www.festivaloffavignon.com/resultats-recherche?recherche="+encodeURIComponent(label))).trim();

    const txt = document.createElement('span');
    txt.style.flex='1 1 auto'; txt.style.overflow='hidden'; txt.style.textOverflow='ellipsis';
    txt.style.cursor='pointer'; txt.textContent = label;
    e.appendChild(txt);

    if (isIOS){
      // icône fiable (tap court)
      const a = document.createElement('a');
      a.textContent = '🔎';
      a.href = href || '#';
      a.target = '_blank';
      a.rel = 'noopener,noreferrer';
      a.title = 'Recherche';
      a.style.flex='0 0 auto'; a.style.textDecoration='none'; a.style.userSelect='none';
      a.addEventListener('click', ev => { ev.stopPropagation(); });
      e.appendChild(a);

      // tap court sur le texte = sélection
      txt.addEventListener('click', () => tapSelect(txt));
    } else {
      // long-press Android/Desktop
      attachLongPress(txt, ()=>href, ()=>tapSelect(txt));
    }

    this.eGui = e;
  }
  getGui(){ return this.eGui; }
  refresh(){ return false; }
}
""")

# JS Code chargé de lancer la recherche d'itinéraire sur la colonne Lieu via icône sur IOS et appui long sur autres plateformes
JS_LIEU_RENDERER = JsCode("""
class LieuRenderer {
  init(params){
    // --- helpers sélection & ouverture ---
    function tapSelect(el){
      const cell = el.closest ? el.closest('.ag-cell') : null;
      if (!cell) return;
      try {
        cell.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
        cell.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
        cell.dispatchEvent(new MouseEvent('click',{bubbles:true}));
      } catch(_){}
    }
    function openNewTab(u){
      if (!u) return;
      try { window.open(u,'_blank','noopener'); }
      catch(_) { window.location.assign(u); }
    }
    // --- long-press universel ---
    function attachLongPress(el, getUrl, onTap){
      const DELAY=550, THRESH=8, TAP_MS=220;
      let sx=0, sy=0, moved=false, pressed=false, startT=0, timer=null, firedLong=false, hadTouchTs=0;
      const now = ()=>Date.now();
      const withinTouchGrace = ()=> (now()-hadTouchTs)<800;
      const clearT=()=>{ if (timer){ clearTimeout(timer); timer=null; } };

      const onDown = ev=>{
        if (ev.type==='mousedown' && withinTouchGrace()) return;
        const t = ev.touches ? ev.touches[0] : ev;
        sx=(t?.clientX)||0; sy=(t?.clientY)||0;
        moved=false; pressed=true; firedLong=false; startT=now();
        clearT(); timer=setTimeout(()=>{ if(pressed && !moved){ firedLong=true; } }, DELAY);
      };
      const onMove = ev=>{
        if(!pressed) return;
        const t = ev.touches ? ev.touches[0] : ev;
        const dx=Math.abs((t?.clientX||0)-sx), dy=Math.abs((t?.clientY||0)-sy);
        if (dx>THRESH || dy>THRESH){ moved=true; clearT(); }
      };
      const onUp = ev=>{
        if (ev.type==='mouseup' && withinTouchGrace()) return;
        const dur = now()-startT, isTap=(dur<TAP_MS)&&!moved;
        pressed=false; clearT();
        if (firedLong && !moved){ openNewTab(getUrl()); return; }
        if (isTap && typeof onTap==='function'){ requestAnimationFrame(()=>onTap()); }
      };
      const onCancel=()=>{ pressed=false; clearT(); };

      if (window.PointerEvent){
        el.addEventListener('pointerdown', onDown, {passive:true});
        el.addEventListener('pointermove', onMove,  {passive:true});
        el.addEventListener('pointerup',   onUp,    {passive:false});
        el.addEventListener('pointercancel', onCancel, {passive:true});
      } else {
        el.addEventListener('touchstart', e=>{ hadTouchTs=now(); onDown(e); }, {passive:true});
        el.addEventListener('touchmove',  onMove,  {passive:true});
        el.addEventListener('touchend',   onUp,    {passive:false});
        el.addEventListener('touchcancel', onCancel, {passive:true});
        el.addEventListener('mousedown',  onDown,  true);
        el.addEventListener('mousemove',  onMove,  true);
        el.addEventListener('mouseup',    onUp,    false);
      }
      el.addEventListener('contextmenu', e=>e.preventDefault(), true);
      el.style.webkitTouchCallout='none';
      el.style.webkitUserSelect='none';
      el.style.userSelect='none';
      el.style.touchAction='manipulation';
    }

    // --- construction du rendu ---
    const e = document.createElement('div');
    e.style.display='flex'; e.style.alignItems='center'; e.style.gap='0.4rem';
    e.style.width='100%'; e.style.overflow='hidden';

    const label = (params.value ?? '').toString().trim();
    const addrEnc = (params.data && params.data.__addr_enc)
      ? String(params.data.__addr_enc).trim()
      : encodeURIComponent(label || "");

    const ctx  = params.context || {};
    const app  = ctx.itineraire_app || "Google Maps Web";
    const plat = ctx.platform || (
      /iPad|iPhone|iPod/.test(navigator.userAgent) ? "iOS"
      : /Android/.test(navigator.userAgent) ? "Android" : "Desktop"
    );

    let url = "#";
    if (addrEnc) {
      if (app === "Apple Maps" && plat === "iOS") {
        url = "http://maps.apple.com/?daddr=" + addrEnc;
      } else if (app === "Google Maps App") {
        if (plat === "iOS")        url = "https://www.google.com/maps/dir/?api=1&destination=" + addrEnc;
        else if (plat === "Android") url = "geo:0,0?q=" + addrEnc;
        else                       url = "https://www.google.com/maps/dir/?api=1&destination=" + addrEnc;
      } else {
        url = "https://www.google.com/maps/dir/?api=1&destination=" + addrEnc;
      }
    }

    const txt = document.createElement('span');
    txt.style.flex='1 1 auto';
    txt.style.overflow='hidden';
    txt.style.textOverflow='ellipsis';
    txt.style.cursor='pointer';
    txt.textContent = label;
    e.appendChild(txt);

    // --- icône cliquable (iOS) ou long-press ailleurs ---
    const ua = navigator.userAgent || "";
    const isIOS =
        /iPad|iPhone|iPod/.test(ua) ||
        (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) ||
        (ua.includes("Mac") && "ontouchend" in window);
    if (isIOS){
      const icon = document.createElement('a');
      icon.textContent = '📍';
      icon.href = url;
      icon.target = '_blank';
      icon.rel = 'noopener';
      icon.style.flex='0 0 auto';
      icon.style.textDecoration='none';
      icon.style.marginLeft='0.4rem';
      icon.title = 'Itinéraire';
      e.appendChild(icon);
    } else {
      attachLongPress(txt, ()=>url, ()=>tapSelect(txt));
    }

    this.eGui = e;
  }
  getGui(){ return this.eGui; }
  refresh(){ return false; }
}
""")

# JS Code censé permettre en complément des inject_ios_xxx_revive (soft, hard, always) de régler le probleme de blocage de l'UI au retour d'une page Web sur IOS 
# lorsque l'on utilise les long press renderers JS_ACTIVITE_LONGPRESS_RENDERER et JS_LIEU_LONGPRESS_RENDERER. Ce mécanisme n'étant pas fonctionnel à 100%, il a 
# été abandonné au profit des JS_ACTIVITE_RENDERER et JS_LIEU_RENDERER qui utilisent un appel de pages web externes via icônes sur IOS (lequel est fiable) et 
# long press sinon.
JS_IOS_SOFT_REVIVE = JsCode("""
    function(params){
    try { params.api.sizeColumnsToFit(); } catch(e){}

    if (window.__iosSoftReviveInstalled) return;
    window.__iosSoftReviveInstalled = true;

    const ua = navigator.userAgent || "";
    const isIOS =
        /iPad|iPhone|iPod/.test(ua) ||
        (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) ||
        (ua.includes("Mac") && "ontouchend" in window);
    function cameFromBackForward(){
        try {
        var nav = performance.getEntriesByType && performance.getEntriesByType('navigation');
        return !!(nav && nav[0] && nav[0].type === 'back_forward');
        } catch(e){ return false; }
    }

    window.addEventListener('pageshow', function(e){
        if (!isIOS) return;
        if (e.persisted || cameFromBackForward()){
        // “soft revive” côté grille (pas de reload)
        try { params.api.deselectAll(); } catch(_) {}
        try { params.api.refreshCells({ force: true }); } catch(_) {}
        try { params.api.redrawRows(); } catch(_) {}
        try { window.dispatchEvent(new Event('resize')); } catch(_) {}

        // astuce : micro reflow de l’iframe
        try { 
            var root = document.documentElement;
            var prev = root.style.webkitTransform;
            root.style.webkitTransform = 'translateZ(0)';
            void root.offsetHeight;
            root.style.webkitTransform = prev || '';
        } catch(_) {}
        }
    }, false);
    }
    """)

# CellEditorParams des colonnes "Date"
JS_DATE_CELL_EDITOR_PARAMS = JsCode(r"""
function(params){
  function intStrToPretty(s){
    if (s == null) return '';
    s = String(s).trim();
    if (s === '') return '';
    if (!/^\d{8}$/.test(s)) return s;
    var y = parseInt(s.slice(0,4),10),
        m = parseInt(s.slice(4,6),10),
        d = parseInt(s.slice(6,8),10);
    var now = new Date();
    if (y === now.getFullYear()){
      return `${String(d).padStart(2,'0')}/${String(m).padStart(2,'0')}`;   // dd/mm
    }
    return `${String(d).padStart(2,'0')}/${String(m).padStart(2,'0')}/${String(y).slice(-2)}`;  // dd/mm/yy
  }

  let raw = params.data ? params.data.__options_date : null;
  let values = [];

  try{
    const arr = Array.isArray(raw) ? raw : (raw ? JSON.parse(raw) : []);
    values = (arr||[]).map(v => (v==null ? '' : String(v).trim()));
  }catch(e){
    if (typeof raw === 'string'){
      values = raw.split(',').map(s=>s.trim());
    }else{
      values = [];
    }
  }

  const cur = (params.value==null)? '' : String(params.value).trim();
  if (cur !== '' && !values.includes(cur)) values.unshift(cur);

  values = Array.from(new Set(values));

  return {
    values: values,
    formatValue: function(v){ return intStrToPretty(v); }
  };
}
""")

# ValueParser des colonnes "Date"
JS_DATE_VALUE_PARSER = JsCode(r"""
function(params){
  var s = (params.newValue==null) ? '' : String(params.newValue).trim();
  if (s === '') return '';
  if (/^\d{8}$/.test(s)) return s;   // déjà un yyyymmdd en str
  return (params.oldValue==null) ? '' : String(params.oldValue).trim();
}
""")

# ValueFormatter des colonnes "Date"
JS_DATE_VALUE_FORMATTER = JsCode(r"""
function(p){
  function intStrToPretty(s){
    if (s == null) return '';
    s = String(s).trim();
    if (s === '') return '';
    if (!/^\d{8}$/.test(s)) return s;
    var y = parseInt(s.slice(0,4),10),
        m = parseInt(s.slice(4,6),10),
        d = parseInt(s.slice(6,8),10);
    var now = new Date();
    if (y === now.getFullYear()){
      return `${String(d).padStart(2,'0')}/${String(m).padStart(2,'0')}`;
    }
    return `${String(d).padStart(2,'0')}/${String(m).padStart(2,'0')}/${String(y).slice(-2)}`;
  }
  return intStrToPretty(p.value);
}
""")

JS_TEL_ICON_RENDERER = JsCode("""
class TelIconRenderer {
  init(params){
    const e = document.createElement('div');
    e.style.display='flex'; e.style.alignItems='center'; e.style.gap='0.5rem';
    e.style.width='100%'; e.style.overflow='hidden';

    const raw = (params.value ?? '').toString().trim();

    // Texte (numéro affiché)
    const txt = document.createElement('span');
    txt.style.flex='1 1 auto';
    txt.style.overflow='hidden';
    txt.style.textOverflow='ellipsis';
    txt.textContent = raw;
    e.appendChild(txt);

    // Normalisation tel:+...
    function normalizeTel(s){
      if (!s) return "";
      s = s.trim();
      // garde un éventuel "+" en tête, enlève tout le reste non-chiffres
      let plus = s.startsWith("+");
      let digits = s.replace(/[^0-9]/g,"");
      if (!digits) return "";
      return (plus ? "tel:+"+digits : "tel:"+digits);
    }

    const href = normalizeTel(raw) || "#";

    // Bouton 📞
    const a = document.createElement('a');
    a.textContent = '📞';
    a.href = href;
    a.title = 'Appeler';
    a.style.textDecoration='none';
    a.style.userSelect='none';
    a.style.flex='0 0 auto';
    // éviter de casser la sélection de la ligne
    a.addEventListener('click', ev => ev.stopPropagation());
    e.appendChild(a);

    this.eGui = e;
  }
  getGui(){ return this.eGui; }
  refresh(){ return false; }
}
""")

JS_WEB_ICON_RENDERER = JsCode("""
class WebIconRenderer {
  init(params) {
    const e = document.createElement('div');
    e.style.display = 'flex';
    e.style.alignItems = 'center';
    e.style.justifyContent = 'center';
    e.style.width = '100%';
    e.style.cursor = 'pointer';

    const url = (params.value || '').trim();
    if (!url) {
      this.eGui = document.createTextNode('');
      return;
    }

    const a = document.createElement('a');
    a.href = url.startsWith('http') ? url : 'https://' + url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.title = 'Ouvrir le site';

    const icon = document.createElement('span');
    icon.textContent = '🌐';
    icon.style.fontSize = '1.1rem';
    icon.style.userSelect = 'none';
    a.appendChild(icon);

    // --- comportement iOS / Safari (ouvrir dans même onglet si nécessaire) ---
    a.addEventListener('click', (ev) => {
      try {
        if (/iPad|iPhone|iPod/.test(navigator.userAgent)) {
          ev.preventDefault();
          window.top.location.href = a.href;
        }
      } catch (e) {}
    });

    e.appendChild(a);
    this.eGui = e;
  }

  getGui() {
    return this.eGui;
  }

  refresh() { return false; }
}
""")

def reprogrammation_request_set(idx, jour):
    st.session_state.setdefault("reprogrammation_request", 
        {
            "idx": idx,
            "jour": jour,
        }
    )

def reprogrammation_request_get():
    return st.session_state.get("reprogrammation_request")

def reprogrammation_request_del():
    if "reprogrammation_request" in st.session_state:
        del st.session_state["reprogrammation_request"]

def row_modification_request_set(idx, cols):
    st.session_state.setdefault("row_modification_request", 
        {
            "idx": idx,
            "cols": cols,
        }
    )

def row_modification_request_get():
    return st.session_state.get("row_modification_request")

def row_modification_request_del():
    if "row_modification_request" in st.session_state:
        del st.session_state["row_modification_request"]

# Affichage d'un dataframe
def afficher_df(
        label, 
        df, 
        hide=[], 
        editable=[], 
        fixed_columns={}, 
        header_names={}, 
        key="affichage_df", 
        colorisation=False, 
        hide_label=False, 
        background_color=None, 
        cell_renderers=None,
        ):

    # Calcul de la hauteur de l'aggrid
    nb_lignes = len(df)
    ligne_px = 30  # hauteur approximative d’une ligne dans AgGrid
    max_height = 250 #150
    height = min(nb_lignes * ligne_px + 50, max_height)

    # Initialisation du compteur qui permet de forcer le réaffichage complet de l'aggrid
    session_state_key_counter = key + "_key_counter"
    st.session_state.setdefault(session_state_key_counter, 0)
    
    # # Initialisation du flag indiquant si l'on est en mode réaffichage complet de l'aggrid
    # session_state_forcer_reaffichage = key + "_forcer_reaffichage"
    # st.session_state.setdefault(session_state_forcer_reaffichage, )
       
    # Initialisation de la variable d'état contenant la requête de selection / déselection
    session_state_sel_request = key + "_sel_request"
    st.session_state.setdefault(session_state_sel_request, copy.deepcopy(SEL_REQUEST_DEFAUT))

    gb = GridOptionsBuilder.from_dataframe(df)

    # Configuration par défaut des colonnes
    gb.configure_default_column(resizable=True)

    # Colonnes à largeur fixe
    for col, width in fixed_columns.items():
        if col in df.columns:
            gb.configure_column(
                col,
                filter=False,
                resizable=False,
                width=width,
                minWidth=width,
                maxWidth=width,
                flex=0,
                suppressSizeToFit=True,
            )

    # header names
    for col, name in header_names.items():
        if col in df.columns:
            gb.configure_column(
                col,
                headerName=name
            )

    # Configuration de la colonne Date
    if "Date" in df.columns:
        gb.configure_column(
            "Date",
            pinned=JsCode("'left'"),
        valueParser=JS_DATE_VALUE_PARSER,
        valueFormatter=JS_DATE_VALUE_FORMATTER,
        )

    #Colonnes cachées
    for col in hide:
        if col in df.columns:
            gb.configure_column(col, hide=True)

    #Colonnes editables
    for col in editable:
        if col in df.columns:
            gb.configure_column(col, editable=True)

    # Colorisation
    if colorisation:
        if "Date" in df.columns:
            df["__jour"] = df["Date"].apply(lambda x: int(str(int(float(x)))[-2:]) if pd.notna(x) else None)
            gb.configure_column("__jour", hide=True)
            gb.configure_grid_options(getRowStyle=JsCode(f"""
            function(params) {{
                const jour = params.data.__jour;
                const couleurs = {PALETTE_COULEURS_JOURS};
                if (jour && couleurs[jour]) {{
                    return {{ 'backgroundColor': couleurs[jour] }};
                }}
                return null;
            }}
            """))
    elif background_color is not None:
        gb.configure_grid_options(getRowStyle=JsCode(f"""
            function(params) {{
                return {{
                    'backgroundColor': '{background_color}'
                }}
            }}
            """)
        )

    # Cell renderers
    if cell_renderers is not None:
        for item in cell_renderers:
            col = item.get("col")
            renderer = item.get("renderer")
            if col in df.columns and renderer is not None:
                gb.configure_column(col, cellRenderer=renderer)

    # Configuration de la sélection
    gb.configure_selection(selection_mode="single", use_checkbox=False) #, pre_selected_rows=[current_selected_row_pos]) 

    # Gestion des sélections / désélections demandées via demander_selection() demander_deselection()
    # Utilise le JS code JS_SELECT_DESELECT_ONCE lequel exploite les colonnes de travail __sel_id, __sel_ver, __desel_id, __desel_ver
    # __sel_id = id de la ligne à sélectionner (None si pas de contrainte de sélection)
    # __sel_ver = version de la demande de sélection (doit être incrémentée à chaque demande)
    # __desel_id = id de la ligne devant rester visible lors de la déselection (None si aucune contrainte de visibilité lors de la désélection)
    # __desel_ver = version de la demande de désélection (doit être incrémentée à chaque demande)
    sel_request_key = key + "_sel_request"
    sel_request = st.session_state.get(sel_request_key)
    gb.configure_column("__desel_ver", hide=True)
    if "__desel_ver" not in df.columns:
        df["__desel_ver"] = sel_request["desel"]["ver"] if sel_request is not None else 0
    gb.configure_column("__desel_id", hide=True)
    if "__desel_id" not in df.columns:
        df["__desel_id"] =  get_uuid(df, sel_request["desel"]["id"]) if sel_request is not None else None
    gb.configure_column("__sel_ver", hide=True)
    if "__sel_ver" not in df.columns:
        df["__sel_ver"] = sel_request["sel"]["ver"] if sel_request is not None else 0
    gb.configure_column("__sel_id", hide=True)
    if "__sel_id" not in df.columns:
        df["__sel_id"] =  get_uuid(df, sel_request["sel"]["id"]) if sel_request is not None else None
    gb.configure_column("__sel_source", hide=True)
    if "__sel_source" not in df.columns:
        df["__sel_source"] = "api"
    
    row = None
    selection_demandee = False
    if sel_request is not None and sel_request["sel"]["pending"]:
        if sel_request["sel"]["id"] is not None:
            reqid = sel_request["sel"]["id"]
            # tracer.log(f"{key}: Traitement de la requête de sélection id {sel_request["sel"]["id"]} ver {sel_request["sel"]["ver"]}")
            df["__sel_id"] = get_uuid(df, reqid)
            df["__sel_ver"] = sel_request["sel"]["ver"]
            if reqid in df.index: 
                row = df.loc[reqid]
                # tracer.log(f"{key}: row = df.loc[{reqid}]")
            selection_demandee = True
        st.session_state[sel_request_key]["sel"]["pending"] = False

    deselection_demandee = False
    if sel_request is not None and sel_request["desel"]["pending"]:
        # tracer.log(f"{key}: Traitement de la requête de desélection ver {sel_request["desel"]["ver"]}")
        df["__desel_ver"] = sel_request["desel"]["ver"]
        df["__desel_id"] = get_uuid(df, sel_request["desel"]["id"]) # id visible après déselection, None si pas de contrainte de visibilité
        df["__sel_id"] = None
        deselection_demandee = True
        st.session_state[sel_request_key]["desel"]["pending"] = False

    gb.configure_grid_options(
        onGridReady=JS_SELECT_DESELECT_ONCE,
    )
    
    # Ajout de la colonne __uuid si elle n'existe pas
    add_persistent_uuid(df)
    if "__uuid" not in hide:
        gb.configure_column("__uuid", hide=True)

    # Mise en page de la grille
    gb.configure_grid_options(onFirstDataRendered=JsCode(f"""
        function(params) {{
            params.api.sizeColumnsToFit();
        }}
    """))

    # Permet de gérer les modifications de df_display dans avoir à redessiner l'aggrid complètement par changement de key
    gb.configure_grid_options(
        immutableData=True,
        deltaRowDataMode=True,
        getRowId=JsCode("function (params) { return params.data.__uuid; }"),
    )

    grid_options = gb.build()
    grid_options["suppressMovableColumns"] = True

    if not hide_label:
        st.markdown(f"##### {label}")

    grid_key = f"_{key} {st.session_state.get(session_state_key_counter)}"
    tracer.log(f"Grid_key: {grid_key}")

    response = AgGrid(
        df,
        gridOptions=grid_options,
        allow_unsafe_jscode=True,
        height=height,
        reload_data=True,
        data_return_mode=DataReturnMode.AS_INPUT,
        update_mode=GridUpdateMode.MODEL_CHANGED | GridUpdateMode.SELECTION_CHANGED,
        key=grid_key,
    )

    event_data = response.get("event_data")
    event_type = event_data["type"] if isinstance(event_data, dict) else None
    tracer.log(f"{key}: event {event_type}", types=["gen", "event"])

    # Récupération du retour grille __sel_source
    # Cette information est passée à la valeur "user" par le JsCode JS_SELECT_DESELECT_ONCE si le cellValueChanged provient d'un click utilisateur.
    # Elle permet de n'effectuer les traitements de cellValueChanged que sur les seuls évènements utilisateurs et de bypasser ceux provenant d'une
    # demande de sélection programmée via demander_selection().
    try:
        df_dom = pd.DataFrame(response["data"]) if "data" in response and isinstance(response["data"], pd.DataFrame) else pd.DataFrame()  
    except:
        df_dom = pd.DataFrame()
        
    if not df_dom.empty:
        first_row = df_dom.iloc[0]
        sel_source = (first_row.get("__sel_source") or "api") # 'user' ou 'api'
        tracer.log(f"{key}: sel_source {sel_source}", types=["sel_source"])

    selected_row_key = key + "_selected_row"
    selected_row_pred = st.session_state.get(selected_row_key, df.iloc[0] if len(df) > 0 else None)

    selected_rows = response["selected_rows"]
    if not selection_demandee:
        if isinstance(selected_rows, pd.DataFrame) and not selected_rows.empty:
            # tracer.log("{key}: row = selected_rows.iloc[0]")
            row = selected_rows.iloc[0] 
        elif isinstance(selected_rows, list) and len(selected_rows) > 0:
            # tracer.log("{key}: row = selected_rows[0]")
            row = selected_rows[0]
        else:
            # tracer.log("{key}: row = selected_row_pred")
            row = selected_row_pred

    st.session_state[sel_request_key]["sel"]["id"] = get_index_from_uuid(df, row["__uuid"]) if row is not None else None
    st.session_state[selected_row_key] = row

    idx = None
    if editable and event_type == "cellValueChanged":
        forcer_reaffichage_df(key)
        try:
            df_dom = pd.DataFrame(response["data"]) if "data" in response and isinstance(response["data"], pd.DataFrame) else pd.DataFrame()  
        except:
            df_dom = pd.DataFrame() 
        
        if not df_dom.empty:
            i, idx = get_ligne_modifiee_uuid(df_dom, df, columns_to_drop=["__uuid", "__desel_ver", "__desel_id", "__sel_ver", "__sel_id", "__sel_source"])
            if i is not None:
                df.loc[idx] = df_dom.loc[i]

    return row, idx

# Affiche le titre de la page de l'application
def afficher_titre(title):
    # Réduire l’espace en haut de la page
    st.markdown(
        """
        <style>
            .block-container {
                padding-top: 2rem;
            }
        </style>
        """, unsafe_allow_html=True
    )

    # Titre de la page
    st.markdown(f"## {title}")

# Affiche l'aide de l'application
def afficher_aide():
    with st.expander("À propos"):
    
        with st.expander("Fonctionnalités générales"):
            st.markdown("""
            <div style='font-size: 14px;'>
            <p style="margin-bottom: 0.2em">Cette application offre les fonctionnalités suivantes:</p>
            <ul style="margin-top: 0em; margin-bottom: 2em">
            <li>Choix de la période à programmer</li>
            <li>Chargement des activités à programmer à partir d'un fichier Excel</li>
            <li>Gestion de la programmation des activités en respectant les règles décrites dans le paragraphe ci-dessous</li>
            <li>Gestion des créneaux disponibles</li>
            <li>Prise en compte optionnelle des pauses (déjeuner, dîner, café)</li>
            <li>Gestion des liens de recherche sur le net</li>
            <li>Sauvegarde des données modifiées dans un fichier téléchargeable</li>
            <li>Fonction défaire / refaire</li>
            <li>Vérification de cohérence des données d'entrée (chevauchements d'activités, marges trop courtes, formats de données)</li>
            </ul>            
            </div>
            """, unsafe_allow_html=True)  

        with st.expander("Règles de programmation des activités"):
            st.markdown("""
            <div style='font-size: 14px;'>
            <p style="margin-bottom: 0.2em">Règles adoptées pour la programmation des activités:</p>
            <ul style="margin-top: 0em; margin-bottom: 0.5em">
            <li>30 minutes de marge entre activités</li>
            <li>1 heure par pause repas</li>
            <li>1/2 heure par pause café sans marge avec l'activité précédente ou suivante</li>
            <li>Respect des jours de relâches</li>
            </ul>
            <p>Ces valeurs sont paramétrables via la rubrique Paramètres.</p>
            </div>
            """, unsafe_allow_html=True)  

        with st.expander("Utilisation"):
            st.markdown("""
            <div style='font-size: 14px;'>
            <p>Les activités à programmer sont présentées dans deux tableaux séparés, 
                l'un pour les activités déja programmées à une date donnée, l'autre pour les activités restant à programmer. 
                Dans ces deux tableaux les informations sont éditables, sauf les heures de fin (qui sont calculées automatiquement) 
                et les dates de programmation, heures de début et durées des activités réservées (celles dont la colonne 'Réservé' est à Oui). 
                Sur la colonne Date un menu permet de programmer / reprogrammer les activités en fonction du jour sélectionné, 
                voire de déprogrammer les activités du tableau des activités programmées par sélection de l'item vide du menu.</p>
                         
            <p>Dans les deux tableaux les colonnes Activité et Lieu permettent respectivement de lancer soit une recherche Web sur l'activité, soit 
                une recherche d'itinéraire sur le lieu de l'activité. Le lien utilisé pour la recherche Web sur l'activité est l'hyperlien 
                mis sur la cellule du tableau Excel d'entrée (lequel est recopié dans la colonne Hyperlien des tableaux d'activités programmées 
                et non programmées). La recherche d'itinéraire quant à elle est réalisée en utilisant l'application choisie dans les paramètres 
                et soit l'adresse du carnet d'adresse située en feuille 2 du tableau Excel d'entrée, soit le nom du lieu et la ville par défaut 
                définie dans les paramètres.</p>
                        
            </p>Dans le tableau des activités programmées la couleur de fond est fonction du jour de programmation 
                et les activités réservées sont écrite en rouge. Dans le tableau des activités non programmées la couleur de fond menthe 
                permet de repérer les activités programmables.</p>
            
            <p>Deux autres tableaux adressent la gestion des créneaux disponibles. 
                Le premier présente les créneaux encore disponibles sur la période considérée et le deuxième les activités programmables dans 
                le créneau sélectionné en tenant compte de leur durée et de la marge entre activités. 
                Un bouton Programmer permet de programmer l'activité programmable sélectionnée au jour dit du créneau sélectionné. 
                la couleur de fond est fonction du jour pour les créneaux disponibles et les activités programmables.</p>
            
            <p>Enfin un dernier tableau présente le carnet d'adresses. Les champs Nom / Adresse / Numéro de Téléphone / Adresse Web de chaque entrée peuvent être édités 
                et le menu correspondant de la barre latérale escamotable permet d'ajouter / supprimer des entrées. Dans les colonnes Tel (Numéro de Téléphone) et Web 
                (Adresse Web) des boutons permettent d'appeler le numéro de téléphone ou aller sur le site Web correspondant.</p>
            
            <p style="margin-bottom: 0.2em">Les menus sont regroupés dans une barre latérale escamotable:</p>
            <ul style="margin-top: 0em">
                <li>Menu Fichier: permet de charger un contexte à partir d'un fichier, initialiser un nouveau contexte, sauvegarder le contexte courant dans un fichier téléchargeable.</li>
                <li>Menu Edition: permet de défaire, refaire une opération.</li>
                <li>Menu Activités: permet sur l'activité séléctionnée dans les tableaux d'activites programmées et non programmées (vous pouvez passer de l'activité sélectionnée dans l'un ou l'autre des tableaux en cliquant sur le champ affichant l'activité courante) de:
                        <ul>
                        <li>rechercher de l'information sur le Web (via un lien Web éditable dans les propriétés),</li> 
                        <li>rechercher un itinaire, sur la base du lieu enregistré pour l'activité (l'application d'itinéraire et la ville de recherche par défaut sont réglables dans la section Paramètres et un carnet d'adresses avec colonnes Nom et Adresse peut être enregistré dans la feuille 2 du fichier Excel d'entrée),</li>
                        <li>supprimer l'activité (si elle n'est pas réservée),</li> 
                        <li>déprogrammer l'activité (si elle est déjà programmée sans être réservée),</li>
                        <li>programmer / reprogrammer l'activité (si elle n'est pas réservée et que d'autres dates de programmation sont possibles)</li>
                        <li>éditer les propriétés l'activité.</li>
                        <li>ajouter une activité. Le champ 'Clipbord' situé sous le bouton d'ajout d'activité permet de coller un texte provenant d'une source exterieure et dans lequel 
                            la fonction d'ajout recherchera les informations à utiliser pour créer la nouvelle activité: Nom, Début, Durée, Lieu, Périodes de relâche.</li>
                        </ul>
                <li>Menu Carnet d'adresses: permet d'ajouter / supprimer des entrées dans le carnet d'adresses.</li>
            </ul>
                        
            <p style="margin-bottom: 0.2em">En haut de la page principale une rubrique escamotable 'Infos' présente:</p>
            <ul style="margin-top: 0em">
                <li>La présente aide.</li>
                <li>Une rubrique présentant les incohérences dans le fichier chargé (notamment les chevauchements de programmation en tenant compte des marges entre activités). 
                    Cette rubrique est mise à jour au fil de l'eau.</li>
                <li>La période programmation: elle est automatiquement déduite des activités renseignées dans le fichier chargé, mais peut être modifiée en cours d'édition. Par défaut l'application recherche les dates de début et de fin du festival de l'année courante.</li>
                <li>Les paramètres de l'application comprennant:
                        <ul>
                        <li>la marge entre activités</li>
                        <li>la durée des pauses repas et café</li>
                        <li>le nom de l'application d'itinéraire (Google Maps, Apple, etc.)</li>
                        <li>la ville de recherche par défaut pour la recherche d'itinéraire</li>
                        <li>la possibilité de choisir si les menus de gestion des activités sont dans la barre latérale ou la page principale.</li>
                        </ul>
                </li>
            </ul>
                        
            <p>A la première utilisation l'application propose à l'utilisateur de créer un espace personnel dans lequel est automatiquement sauvegardé le contexte de travail (l'adresse de cet espace est : adresse de l'application/?user_id=id utilisateur).
                En cas de rupture de connexion avec le serveur, le travail en cours est ainsi automatiquement restitué à la prochaine reconnexion.</p>
            </div>
            """, unsafe_allow_html=True)  

        with st.expander("Format des données"):
            st.markdown("""
            <div style='font-size: 14px;'>
            <p style="margin-bottom: 0.2em">Le fichier Excel d'entrée doit contenir en feuille 1 les colonnes suivantes:</p>
            <ul style="margin-top: 0em; margin-bottom: 2em">
                <li>Date : Date de l'activité (entier)</li>
                <li>Début : Heure de début de l'activité (format HHhMM)</li>
                <li>Fin : Heure de fin de l'activité (format HHhMM)</li>
                <li>Durée : Durée de l'activité (format HHhMM ou HHh)</li>
                <li>Activité : Nom de l'activité (nom de spectacle, pause, visite, ...)</li>
                <li>Lieu : Lieu de l'activité</li>
                <li>Relâche : Jours / périodes de relâche ou de validité de l'activité (voir ci-dessous les formats acceptés)</li>
                <li>Réservé : Indique si l'activité est réservée (Oui/Non, vide interpété comme Non)</li>
            </ul>

            <p style="margin-bottom: 0.2em">Les jours / périodes de relâche ou de validité de l'activité sont une suite séparée par des virgules de spécifications répondant aux règles suivantes:</p>
            <ul style="margin-top: 0em; margin-bottom: 2em">
                <li>Suite de dates de relâche de type jour ou jour/mois ou jour/mois/année, séparées par des virgules (mois ou année omis -> mois et année en cours implicites) </li>
                <li>Regroupement de jours de relâche : (j1, j2, ...)/mois ou (j1, j2, ...)/mois/année</li>
                <li>Intervalle de dates de relâche: [dmin-dmax] ou [jmin-jmax]/mois ou /mois/année</li>
                <li>Intervalle de dates de validité: <dmin-dmax> ou ![jmin-jmax]/mois ou /mois/année</li>
                <li>Spécification de jours pairs ou impairs: 'pair(s)' / 'impair(s)'</li>
                <li>Exemple: '<5-26>/07, 04/07/25, (8,10)/07, [20-22]/07, jours pairs' -> activité disponible du 5 au 26 juillet de l'année en cours,
                sauf le 04/07/2025, les 8 et 10 juillet de l'année en cours, entre le 20 et le 22 juillet de l'année en cours et les jours pairs.</li>
            </ul>
                        
            <p style="margin-bottom: 0.2em">En feuille 2 peut figurer un carnet d'adresses des lieux d'activités, utilisé pour la recherche d'itinéraire. 
            Il doit comprendre les colonnes suivantes:</p>
            <ul style="margin-top: 0em; margin-bottom: 2em">
                <li>Nom : nom devant figurer dans la colonne Lieu des tableaux d'activités pour que l'adresse associée soit utilisée dans la recherche d'itinéraire</li>
                <li>Adresse : adresse utilisée pour la recherche d'itinéraire</li>
                <li>Tel : numéro de téléphone</li>
                <li>Web : adresse du site Web</li>
            </ul>

            <p>📥Un modèle Excel est disponible <a href="https://github.com/jnicoloso-91/PlanifAvignon-05/raw/main/Mod%C3%A8le%20Excel.xlsx" download>
            ici
            </a></p>
            <p>ℹ️ Si le téléchargement ne démarre pas, faites un clic droit → "Enregistrer le lien sous...".</p>
            </div>
            """, unsafe_allow_html=True)  

# Affichage de la période de programmation
def afficher_periode_programmation():
    with st.expander("Période de programmation", expanded=False):

        changed_keys = []
        need_maj_contexte = False

        if st.session_state.get("periode_programmation_abandon_pending", False) == True:
            st.session_state.periode_debut_input = st.session_state.periode_a_programmer_debut
            st.session_state.periode_fin_input = st.session_state.periode_a_programmer_fin
            st.session_state.periode_programmation_abandon_pending = False

        with st.form("periode_programmation_form"):
            base_deb = st.session_state.periode_a_programmer_debut
            base_fin = st.session_state.periode_a_programmer_fin

            deb_kwargs = dict(key="periode_debut_input", format="DD/MM/YYYY")
            fin_kwargs = dict(key="periode_fin_input",   format="DD/MM/YYYY")

            # init une seule fois
            if "periode_debut_input" not in st.session_state:
                st.session_state.periode_debut_input = base_deb
            if "periode_fin_input" not in st.session_state:
                st.session_state.periode_fin_input = base_fin

            # Prise en compte des valeurs du modèle si l'app les a recalculées par ailleurs
            push_modele_values = st.session_state.get("push_periode_programmation_modele_values", True)
            if push_modele_values and "periode_a_programmer_debut" in st.session_state and "periode_a_programmer_fin" in st.session_state:
                st.session_state.periode_debut_input = st.session_state.periode_a_programmer_debut
                st.session_state.periode_fin_input = st.session_state.periode_a_programmer_fin
                st.session_state["push_periode_programmation_modele_values"] = False

            # Surtout: ne PAS mettre deb_kwargs["value"] / fin_kwargs["value"]
            # -> st.date_input lira directement st.session_state[<key>]

            dates_valides = get_dates_from_df(st.session_state.df)  # doit renvoyer une série d'int (jours)
            date_min = int(dates_valides.min()) if not dates_valides.empty else None
            date_max = int(dates_valides.max()) if not dates_valides.empty else None

            if isinstance(date_min, int):
                try:
                    if date_min is not None:
                        deb_kwargs["max_value"] = base_deb.replace(day=int(str(date_min)[-2:]))
                except ValueError as e:
                    print(f"Erreur dans afficher_periode_programmation: {e}")
            if isinstance(date_max, int):
                try:
                    if date_max is not None:
                        fin_kwargs["min_value"] = base_fin.replace(day=int(str(date_max)[-2:]))
                except ValueError as e:
                    print(f"Erreur dans afficher_periode_programmation: {e}")

            try:
                col1, col2 = st.columns(2)
                with col1:
                    debut = st.date_input("Début", **deb_kwargs)
                with col2:
                    fin   = st.date_input("Fin", **fin_kwargs)

            except Exception as e:
                print(f"Erreur dans afficher_periode_programmation : {e}")
        

            col1, col2 = st.columns(2)
            appliquer = col1.form_submit_button("Appliquer", use_container_width=True)
            abandonner = col2.form_submit_button("Abandonner", use_container_width=True)

        if appliquer:
            undo.save_prepare()
            if debut != st.session_state.periode_a_programmer_debut:
                st.session_state.periode_a_programmer_debut = debut
                changed_keys.append("periode_a_programmer_debut")
                need_maj_contexte = True

            if fin != st.session_state.periode_a_programmer_fin:
                st.session_state.periode_a_programmer_fin = fin
                changed_keys.append("periode_a_programmer_fin")
                need_maj_contexte = True
            
            # Ne forcer le réaffichage des grilles qu'une seule fois
            if need_maj_contexte:
                undo.save_finalize()
                maj_contexte(maj_donnees_calculees=False)
                # forcer_reaffichage_df("creneaux_disponibles")

            # Sauvegarde en batch (une seule fois)
            if changed_keys:
                for k in changed_keys:
                    try:
                        sql.sauvegarder_param(k)  
                    except Exception  as e:
                        print(f"Erreur dans afficher_periode_programmation : {e}")

                # Pas de st.rerun() nécessaire : submit a déjà provoqué un rerun
                st.toast("Paramètres appliqués.", icon="✅")

        if abandonner:
            st.session_state.periode_programmation_abandon_pending = True
            st.rerun()

def afficher_parametres():

    def ajouter_sans_doublon(liste, val):
        if val not in liste:
            liste.append(val)

    def get_itin_options(platform):
        if platform == "iOS":
            itin_options = ["Apple Maps", "Google Maps"]
        elif platform == "Android":
            itin_options = ["Google Maps"]
        else:
            itin_options = ["Google Maps"]
        return itin_options

    with st.expander("Paramètres", expanded=False):

        # Recupération de la plateforme
        platform = get_platform()  # "iOS" | "Android" | "Desktop"/None

        changed_keys = []
        need_maj_contexte = False

        if st.session_state.get("param_abandon_pending", False) == True:
            st.session_state.param_marge_min = minutes(st.session_state.MARGE)
            st.session_state.param_repas_min = minutes(st.session_state.DUREE_REPAS)
            st.session_state.param_cafe_min  = minutes(st.session_state.DUREE_CAFE)
            st.session_state.itineraire_app_selectbox = st.session_state.itineraire_app
            st.session_state.city_default_input = st.session_state.city_default
            st.session_state.param_abandon_pending = False

        with st.form("params_form"):

            # Marge entre activités
            if st.session_state.get("MARGE") is None:
                st.session_state.MARGE = MARGE
                sql.sauvegarder_param("MARGE")  

            st.session_state.setdefault("param_marge_min", minutes(st.session_state.MARGE))
            st.slider(
                "Marge entre activités (minutes)",
                min_value=0, max_value=120, step=5,
                value=st.session_state.param_marge_min,
                key="param_marge_min",
                help="Marge utilisée pour le calcul des créneaux disponibles. Pour les pauses café, ne s’applique qu’à l’activité précédente OU suivante, la pause café étant supposée se tenir près du lieu de l'une ou de l'autre."
            )

            # Durée des pauses repas
            if st.session_state.get("DUREE_REPAS") is None:
                st.session_state.DUREE_REPAS = DUREE_REPAS
                sql.sauvegarder_param("DUREE_REPAS")  

            st.session_state.setdefault("param_repas_min", minutes(st.session_state.DUREE_REPAS))
            st.slider(
                "Durée des pauses repas (minutes)",
                min_value=0, max_value=120, step=5,
                value=st.session_state.param_repas_min,
                key="param_repas_min",
                help="Durée utilisée pour les pauses repas."
            )

            # Durée des pauses café
            if st.session_state.get("DUREE_CAFE") is None:
                st.session_state.DUREE_CAFE = DUREE_CAFE
                sql.sauvegarder_param("DUREE_CAFE")  

            st.session_state.setdefault("param_cafe_min",  minutes(st.session_state.DUREE_CAFE))
            st.slider(
                "Durée des pauses café (minutes)",
                min_value=0, max_value=120, step=5,
                value=st.session_state.param_cafe_min,
                key="param_cafe_min",
                help="Durée utilisée pour les pauses café."
            )

            # Application itinéraire
            itin_options = get_itin_options(platform)
            if st.session_state.get("itineraire_app") is None or st.session_state.get("itineraire_app") not in itin_options:
                st.session_state.itineraire_app = itin_options[0]
                sql.sauvegarder_param("itineraire_app")  
                        
            index = itin_options.index(st.session_state.itineraire_app) if "itineraire_app_selectbox" not in st.session_state else itin_options.index(st.session_state.itineraire_app_selectbox)
            st.selectbox(
                "Application itinéraire",
                options=itin_options,
                index=index, 
                key="itineraire_app_selectbox",
                help="Sur IOS : Apple/Google Maps. Sinon : Google Maps."
            )

            # Ville par défaut pour la recherche d'itinéraire
            if st.session_state.get("city_default") is None:
                st.session_state.city_default = "Avignon"
                sql.sauvegarder_param("city_default")  

            st.session_state.setdefault("city_default_input", st.session_state.city_default)
            st.text_input(
                "Ville par défaut pour la recherche d'itinéraire",
                value=st.session_state.city_default_input,
                key="city_default_input",
                help="Si vide, la ville du lieu de l’activité est utilisée pour la recherche d'itinéraire."
            )

            col1, col2 = st.columns(2)
            appliquer = col1.form_submit_button("Appliquer", use_container_width=True)
            abandonner = col2.form_submit_button("Abandonner", use_container_width=True)

        if appliquer:
            undo.save_prepare()

            # MARGE
            new_marge = datetime.timedelta(minutes=st.session_state.param_marge_min)
            if st.session_state.MARGE != new_marge:
                st.session_state.MARGE = new_marge
                ajouter_sans_doublon(changed_keys, "MARGE")
                need_maj_contexte = True

            # DUREE_REPAS
            new_repas = datetime.timedelta(minutes=st.session_state.param_repas_min)
            if st.session_state.DUREE_REPAS != new_repas:
                st.session_state.DUREE_REPAS = new_repas
                ajouter_sans_doublon(changed_keys, "DUREE_REPAS")
                need_maj_contexte = True

            # DUREE_CAFE
            new_cafe = datetime.timedelta(minutes=st.session_state.param_cafe_min)
            if st.session_state.DUREE_CAFE != new_cafe:
                st.session_state.DUREE_CAFE = new_cafe
                ajouter_sans_doublon(changed_keys, "DUREE_CAFE")
                need_maj_contexte = True

            # Itinéraire
            new_itineraire = st.session_state.itineraire_app_selectbox
            if st.session_state.itineraire_app != new_itineraire:
                st.session_state.itineraire_app = new_itineraire
                ajouter_sans_doublon(changed_keys, "itineraire_app")

            # Ville par défaut
            new_city = st.session_state.city_default_input.strip()
            if st.session_state.city_default != new_city:
                st.session_state.city_default = new_city
                ajouter_sans_doublon(changed_keys, "city_default")

            # Mise à jour de contexte (seulement si nécessaire car opération lourde)
            if need_maj_contexte:
                undo.save_finalize()
                maj_contexte(maj_donnees_calculees=False)

            # Sauvegarde des paramètres modifiés
            if changed_keys:
                for k in changed_keys:
                    try:
                        sql.sauvegarder_param(k)  
                    except Exception  as e:
                        print(f"Erreur dans afficher_parametres : {e}")
            
            st.toast("Paramètres appliqués.", icon="✅")

        if abandonner:
            st.session_state.param_abandon_pending = True
            st.rerun()

# Affiche le bouton de recharche sur le web
def afficher_bouton_web(nom_activite, disabled=False):    

    #Retour si nom activité vide
    if pd.isna(nom_activite):
        return
                
    # Initialiser le dictionnaire si nécessaire
    if "liens_activites" not in st.session_state:
        st.session_state["liens_activites"] = {}

    liens = st.session_state["liens_activites"]

    # Vérifier si un lien existe déjà
    if nom_activite in liens:
        url = liens[nom_activite]
    else:
        # Construire l'URL de recherche
        url = f"https://www.festivaloffavignon.com/resultats-recherche?recherche={nom_activite.replace(' ', '+')}"
        if nom_activite in liens:
            liens[nom_activite] = url  # L'enregistrer dans la session

    st.link_button(LABEL_BOUTON_CHERCHER_WEB, url, use_container_width=CENTRER_BOUTONS, disabled=disabled)

# Affiche le bouton de recherche d'itinéraire
def afficher_bouton_itineraire(lieu, disabled=False):  

    # Bouton désactivé si lieu vide ou None
    if pd.isna(lieu) or not str(lieu).strip():
        st.link_button(
            LABEL_BOUTON_CHERCHER_ITINERAIRE,
            "#",  # pas de lien cliquable
            use_container_width=CENTRER_BOUTONS,
            disabled=True
        )
        return
    
     # Résolution depuis carnet + fallback
    addr_human, addr_enc = resolve_address_fast(lieu, st.session_state.ca, city_default=st.session_state.city_default)
    itineraire_app = st.session_state.get("itineraire_app", "Google Maps")
    platform = get_platform()  

    if itineraire_app == "Apple Maps" and platform == "iOS":
        url = f"http://maps.apple.com/?daddr={addr_enc}"

    elif itineraire_app == "Google Maps":
        if platform == "iOS":
            url = f"comgooglemaps://?daddr={addr_enc}"
        elif platform == "Android":
            url = f"geo:0,0?q={addr_enc}"
        else:
            # Sur desktop, on retombe sur la version web
            url = f"https://www.google.com/maps/dir/?api=1&destination={addr_enc}"

    else:  # Google Maps
        url = f"https://www.google.com/maps/dir/?api=1&destination={addr_enc}"

    st.link_button(
        LABEL_BOUTON_CHERCHER_ITINERAIRE,
        url,
        use_container_width=CENTRER_BOUTONS,
        disabled=disabled or not addr_enc
    )

# Ajout d'une nouvelle activité 
def afficher_bouton_nouvelle_activite(disabled=False, key="ajouter_activite"):
    import numpy as np

    df = st.session_state.df

    # Initialiser le DataFrame dans session_state si absent
    if "compteur_activite" not in st.session_state:
        st.session_state.compteur_activite = 0

    # Bouton Ajouter
    if st.button(LABEL_BOUTON_NOUVELLE_ACTIVITE, use_container_width=CENTRER_BOUTONS, disabled=disabled, key=key):

        undo.save()

        infos_collage = parse_listing_text(st.session_state.zone_collage or "")
        
        new_idx = ajouter_activite(
            debut=infos_collage["Debut"], 
            duree=infos_collage["Duree"], 
            nom=infos_collage["Activite"], 
            lieu=infos_collage["Lieu"], 
            relache=infos_collage["Relache"],
            hyperlien=infos_collage["Hyperlien"],
        )

        demander_selection("activites_non_programmees", new_idx, deselect="activites_programmees")
        st.session_state.editeur_activite_idx = new_idx
        
        # Bascule du menu activité sur le menu_activites_non_programmees
        st.session_state.menu_activites = {
            "menu": "menu_activites_non_programmees",
            "index_df": new_idx
        }

        # forcer_reaffichage_df("activites_programmables")
        sql.sauvegarder_row(new_idx)
        st.rerun()

# DialogBox de suppression d'activité
@st.dialog("Suppression activité")
def show_dialog_supprimer_activite(df, index_df, df_display):
    st.markdown("Voulez-vous supprimer cette activité ?")
    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button(LABEL_BOUTON_VALIDER, use_container_width=CENTRER_BOUTONS):
            undo.save()
            if est_activite_programmee(df.loc[index_df]):
                demander_selection("activites_programmees", ligne_voisine_index(df_display, index_df), deselect="activites_non_programmees")
            else:
                demander_selection("activites_non_programmees", ligne_voisine_index(df_display, index_df), deselect="activites_programmees")
            # forcer_reaffichage_df("creneaux_disponibles")
            supprimer_activite(index_df)
            sql.sauvegarder_row(index_df)
            st.rerun()
    with col2:
        if st.button(LABEL_BOUTON_ANNULER, use_container_width=CENTRER_BOUTONS):
            st.rerun()

# DialogBox de reprogrammation d'activité programmée
@st.dialog("Reprogrammation activité")
def show_dialog_reprogrammer_activite_programmee(df, activites_programmees, index_df):
    jour_escape = "Aucune" # escape pour déprogrammer l'activité
    jours_possibles = get_jours_possibles(df, activites_programmees, index_df)
    jours_label = [dateint_to_str(x) for x in jours_possibles] + [jour_escape]
    selection = st.selectbox("Choisissez une nouvelle date pour cette activité :", jours_label, key = "ChoixJourReprogrammationActiviteProgrammee")
    jour_selection = date_to_dateint(selection)
    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button(LABEL_BOUTON_VALIDER, use_container_width=CENTRER_BOUTONS):
            if jour_selection == jour_escape:
                # Déprogrammation
                undo.save()
                demander_selection("activites_non_programmees", index_df, deselect="activites_programmees")
                deprogrammer_activite_programmee(index_df)
                # forcer_reaffichage_df("creneaux_disponibles")
                sql.sauvegarder_row(index_df)
                st.rerun()
            else:
                # Reprogrammation 
                jour_choisi = jour_selection
                undo.save()
                demander_selection("activites_programmees", index_df, deselect="activites_non_programmees")
                df.at[index_df, "Date"] = jour_choisi
                sql.sauvegarder_row(index_df)
                st.rerun()
    with col2:
        if st.button(LABEL_BOUTON_ANNULER, use_container_width=CENTRER_BOUTONS):
            st.rerun()

# DialogBox de programmation d'activité non programmée
@st.dialog("Programmation activité")
def show_dialog_programmer_activite_non_programmee(df, activites_programmees, index_df):
    jours_possibles = get_jours_possibles(df, activites_programmees, index_df)
    jours_label = [dateint_to_str(x) for x in jours_possibles]
    selection = st.selectbox("Choisissez une date pour cette activité :", jours_label, key = "ChoixJourProgrammationActiviteNonProgrammee")
    jour_selection = date_to_dateint(selection)
    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button(LABEL_BOUTON_VALIDER, use_container_width=CENTRER_BOUTONS):
            # Programmation à la date choisie
            jour_choisi = jour_selection
            undo.save()
            demander_selection("activites_programmees", index_df, deselect="activites_non_programmees")
            df.at[index_df, "Date"] = jour_choisi
            # forcer_reaffichage_df("creneaux_disponibles")
            sql.sauvegarder_row(index_df)
            st.rerun()
    with col2:
        if st.button(LABEL_BOUTON_ANNULER, use_container_width=CENTRER_BOUTONS):
            st.rerun()

# Initialisation des grid_options sur la grille des activités programmées
def init_activites_programmees_grid_options(df_display):

    gb = GridOptionsBuilder.from_dataframe(df_display)

    # Configuration par défaut des colonnes
    gb.configure_default_column(resizable=True) 

    # Colonnes à largeur fixe
    colonnes_fixes = {"Date": 55, "Début": 55, "Fin": 55, "Durée": 55}
    for col, width in colonnes_fixes.items():
        gb.configure_column(
            col,
            filter=False,
            resizable=False,
            width=width,
            minWidth=width,
            maxWidth=width,
            flex=0,
            suppressSizeToFit=True,
        )

    # Epinglage de la colonne Date
    gb.configure_column(
        "Date",
        pinned=JsCode("'left'")
    )

    # Masquage des colonnes de travail
    work_cols = ACTIVITES_PROGRAMMEES_WORK_COLS
    for c in work_cols:
        gb.configure_column(c, hide=True)

    # Colonnes editables
    non_editable_cols = ["Fin"] + work_cols
    for col in df_display.columns:
        gb.configure_column(col, editable=(col not in non_editable_cols))

    gb.configure_column(
        "Début",
        editable=JsCode("function(params) { return params.data.__non_reserve; }")
    )

    gb.configure_column(
        "Durée",
        editable=JsCode("function(params) { return params.data.__non_reserve; }")
    )

    # Configuration de la colonne "Date"
    # gb.configure_column(
    #     "Date",
    #     editable=True,
    #     cellEditor="agSelectCellEditor",
    #     cellEditorParams=JsCode("""
    #         function(params) {
    #             let raw = params.data.__options_date;
    #             let values = [];

    #             try {
    #                 values = JSON.parse(raw);
    #             } catch (e) {
    #                 values = [];
    #             }

    #             return { values: values };
    #         }
    #     """),
    #     # valueParser=JS_DATE_VALUE_PARSER,
    #     # valueFormatter=JS_DATE_VALUE_FORMATTER,
    # )
    gb.configure_column(
        "Date",
        editable=True,
        cellEditor="agSelectCellEditor",
        cellEditorParams=JS_DATE_CELL_EDITOR_PARAMS,
        valueParser=JS_DATE_VALUE_PARSER,
        valueFormatter=JS_DATE_VALUE_FORMATTER,
    )

    # Configuration de l'appui long pour la recherche Web et la recherche d'itinéraire
    gb.configure_column("Activité", editable=True, cellRenderer=JS_ACTIVITE_RENDERER) #, minWidth=220)
    gb.configure_column("Lieu",     editable=True, cellRenderer=JS_LIEU_RENDERER) #, minWidth=200)

    # Colorisation
    gb.configure_grid_options(getRowStyle=JsCode(f"""
        function(params) {{
            const jour = params.data.__jour;
            const couleurs = {PALETTE_COULEURS_JOURS};
            let style = {{}};

            if (jour && couleurs[jour]) {{
                style.backgroundColor = couleurs[jour];
            }}

            if (params.data.__non_reserve === false) {{
                style.color = 'red';
            }}

            return style;
        }}
    """))

    # Configuration de la sélection
    gb.configure_selection(selection_mode="single", use_checkbox=False) 
    
    gb.configure_grid_options(
        getRowNodeId=JsCode("function(data) { return String(data.__uuid); }"),
        getRowId=JsCode("function(p){ return String(p.data.__uuid); }"),
        columnTypes={"textColumn": {}},  # évite l'erreur #36
        onGridReady=JS_SELECT_DESELECT_ONCE,
    )

    # Mise en page de la grille 
    gb.configure_grid_options(onFirstDataRendered=JsCode(f"""
        function(params) {{
            params.api.sizeColumnsToFit();
        }}
    """))

    grid_options = gb.build()

    # Empêche la possibilité de réorganiser les colonnes
    grid_options["suppressMovableColumns"] = True

    # Supprime le highlight de survol qui pose problème sur mobile et tablette
    grid_options["suppressRowHoverHighlight"] = True

    # Enregistre dans le contexte les paramètres nécessaires à la recherche d'itinéraire (voir JS_LIEU_xxx_RENDERER)
    grid_options["context"] = {
        "itineraire_app": st.session_state.get("itineraire_app", "Google Maps"),
        "platform": get_platform(),  # "iOS" / "Android" / "Desktop"
    }

    return grid_options

# Affiche les activités programmées dans un tableauflag allow_unsafe_jscode is on. AgGrid.tsx:124:15
def afficher_activites_programmees():

    df = st.session_state.get("df")
    if df is None :
        return

    df_display = st.session_state.get("activites_programmees_df_display")
    if df_display is None :
        return

    work_cols = ACTIVITES_PROGRAMMEES_WORK_COLS
    non_editable_cols = ["Fin"] + work_cols

    # Calcul de la hauteur de l'aggrid
    nb_lignes = len(df_display)
    ligne_px = 30  # hauteur approximative d’une ligne dans AgGrid
    max_height = 250
    height = min(nb_lignes * ligne_px + 50, max_height)

    # Initialisation du compteur qui permet de savoir si l'on doit forcer le réaffichage complet de l'aggrid  
    st.session_state.setdefault("activites_programmees_key_counter", 0)

    # Initialisation de la variable d'état indiquant s'il convient de bypasser la section d'édition de cellule 
    st.session_state.setdefault("activites_programmees_bypass_cell_edit", False)

    # Initialisation de la variable d'état contenant la requête de selection / déselection
    st.session_state.setdefault("activites_programmees_sel_request", copy.deepcopy(SEL_REQUEST_DEFAUT))
   
    # Gestion des sélections / désélections demandées via demander_selection() demander_deselection()
    # Utilise le JS code JS_SELECT_DESELECT_ONCE lequel exploite les colonnes de travail suivantes:
    # __sel_id = id de la ligne à sélectionner (None si pas de contrainte de sélection)
    # __sel_ver = version de la demande de sélection (doit être incrémentée à chaque demande)
    # __desel_id = id de la ligne devant rester visible lors de la déselection (None si aucune contrainte de visibilité lors de la désélection)
    # __desel_ver = version de la demande de désélection (doit être incrémentée à chaque demande)
    # __sel_source = information renvoyée par le DOM (event.source exposé par onRowSelected) indiquant si la source de selection est "user" ou "api" selon que la demande de sélection provient d'un click utilisateur ou d'une requête python via JsCode
    # Ces colonnes sont configurées par les fonctions utilisateur demander_selection() et demander_deselection()
    # L'information de retour __sel_source est exploitée par le mecanisme de flip flop entre grille A et grille B
    # via le champ "data" de la réponse de l'aggrid (cf. fonction afficher_activites_programmees() et afficher_activites_non_programmees())
    row = None
    selection_demandee = False
    sel_request = st.session_state.get("activites_programmees_sel_request")
    if sel_request["sel"]["pending"]:
        if sel_request["sel"]["id"] is not None:
            reqid = sel_request["sel"]["id"]
            # tracer.log(f"Traitement de la requête de sélection id {sel_request["sel"]["id"]} ver {sel_request["sel"]["ver"]}")
            df_display["__sel_id"] = get_uuid(df_display, reqid)
            df_display["__sel_ver"] = sel_request["sel"]["ver"]
            if reqid in df_display.index: 
                row = df_display.loc[reqid]
                # tracer.log(f"row = df_display.loc[{reqid}]")
            selection_demandee = True
        st.session_state.activites_programmees_sel_request["sel"]["pending"] = False

    deselection_demandee = False
    if sel_request["desel"]["pending"]:
        # tracer.log(f"Traitement de la requête de desélection ver {sel_request["desel"]["ver"]}")
        df_display["__desel_ver"] = sel_request["desel"]["ver"]
        df_display["__desel_id"] = get_uuid(df_display, sel_request["desel"]["id"]) # id visible après déselection, None si pas de contrainte de visibilité
        df_display["__sel_id"] = None
        deselection_demandee = True
        st.session_state.activites_programmees_sel_request["desel"]["pending"] = False
        
    # if len(df_display) > 0:
    #     tracer.log(f"df_display['__sel_id'] {df_display.iloc[0]["__sel_id"]} df_display['__sel_ver'] {df_display.iloc[0]["__sel_ver"]} df_display['__desel_ver'] {df_display.iloc[0]["__desel_ver"]}")

    grid_options = init_activites_programmees_grid_options(df_display)

    # Affichage
    with st.expander("**Activités programmées**", expanded=True):
        response = AgGrid(
            df_display,
            gridOptions=grid_options,
            allow_unsafe_jscode=True,
            height=height,
            reload_data=True,
            data_return_mode=DataReturnMode.AS_INPUT,
            key=f"Activités programmées {st.session_state.activites_programmees_key_counter}"  # incrémentation de la clef permet de forcer le reaffichage 
        )

        # Affichage de l'erreur renvoyée par le précédent run
        erreur = st.session_state.get("aggrid_activites_programmees_erreur") 
        if erreur is not None:
            st.error(erreur)

        event_data = response.get("event_data")
        event_type = event_data["type"] if isinstance(event_data, dict) else None
        tracer.log(f"event {event_type}", types=["gen", "event"])

        # Pas d'event aggrid à traiter si event_type is None (i.e. le script python est appelé pour autre chose qu'un event aggrid)
        if event_type is None:
            if len(df_display) == 0:
                if st.session_state.menu_activites["menu"] == "menu_activites_programmees":
                    st.session_state.menu_activites = {
                        "menu": "menu_activites_programmees",
                        "index_df": None
                    }
            return

        # Récupération du retour grille __sel_source
        # Cette information est passée à la valeur "user" par le JsCode JS_SELECT_DESELECT_ONCE si le cellValueChanged provient d'un click utilisateur.
        # Elle permet de n'effectuer les traitements de cellValueChanged que sur les seuls évènements utilisateurs et de bypasser ceux provenant d'une
        # demande de sélection programmée via demander_selection().
        sel_source = "unknown"
        try:
            df_dom = pd.DataFrame(response["data"]) if "data" in response and isinstance(response["data"], pd.DataFrame) else pd.DataFrame()  
        except:
            df_dom = pd.DataFrame() 
        if not df_dom.empty:
            first_row = df_dom.iloc[0]
            sel_source = (first_row.get("__sel_source") or "api") # 'user' ou 'api'
            tracer.log(f"sel_source {sel_source}", types=["sel_source"])

        # Récupération de la ligne sélectionnée courante
        selected_rows = response["selected_rows"] if "selected_rows" in response else None
        if not selection_demandee:
            if isinstance(selected_rows, pd.DataFrame) and not selected_rows.empty:
                # tracer.log("row = selected_rows.iloc[0]")
                row = selected_rows.iloc[0] 
            elif isinstance(selected_rows, list) and len(selected_rows) > 0:
                # tracer.log("row = selected_rows[0]")
                row = selected_rows[0]

        # 🟡 Traitement si ligne sélectionnée et index correspondant non vide
        if row is not None:

            # Récupération de l'index de ligne sélectionnée
            index_df = row["__index"]

            # Evènement de type "selectionChanged" 
            if event_type == "selectionChanged":
                # tracer.log(f"Selected row {selected_rows.iloc[0]["__index"] if isinstance(selected_rows, pd.DataFrame) and not selected_rows.empty else (selected_rows[0]["__index"] if isinstance(selected_rows, list) and len(selected_rows) > 0 else None)}")
                if index_df != st.session_state.activites_programmees_sel_request["sel"]["id"] and not deselection_demandee and sel_source == "user":
                    # tracer.log(f"***activites_programmees_sel_request[id] de {st.session_state.activites_programmees_sel_request["sel"]["id"]} à {index_df}")
                    st.session_state.activites_programmees_sel_request["sel"]["id"] = index_df
                    # tracer.log(f"***demander_deselection activites_non_programmees")
                    demander_deselection("activites_non_programmees")
                    
                    # time.sleep(0.05) # Hack défensif pour éviter les erreurs Connection error Failed to process a Websocket message Cached ForwardMsg MISS

                    if not st.session_state.forcer_menu_activites_non_programmees:
                        st.session_state.editeur_activite_idx = index_df
                        st.session_state.menu_activites = {
                            "menu": "menu_activites_programmees",
                            "index_df": index_df
                        }
                    st.rerun()
                else:
                    if st.session_state.forcer_menu_activites_programmees or st.session_state.forcer_maj_menu_activites_programmees:
                        st.session_state.editeur_activite_idx = index_df
                        st.session_state.menu_activites = {
                            "menu": "menu_activites_programmees",
                            "index_df": index_df
                        }
                        st.session_state.forcer_maj_menu_activites_programmees = False
                        
            # Gestion des modifications de cellules
            # Attention : la modification de cellule uniquement sur "cellValueChanged" n'est pas suffisante, car lorsque l'on valide la modification
            # de cellule en cliquant sur une autre ligne, on reçoit un event de type "selectionChanged" et non "cellValueChanged". Mais cela implique 
            # que toute modification programmée de cellule (via l'éditeur d'activité ou les boutons de programmation) va engendrée un écart entre le 
            # df_display modifié par programmation et le df_dom revenant du DOM, ce qui, via le code ci-dessous, va déclencher une modification inverse 
            # à celle souhaitée. Pour eviter cela il faut :
            # 1. Mettre en place un mécanisme de requête de modification qui bypasse la modification de cellule tant que le DOM n'a pas enregistré les 
            #    modifications demandées via le df_display (voir reprogrammation_request et row_modification_request).
            # 2. S'assurer que le DOM renvoie bien via response["data"] les modifications enregistrées. Ceci est réalisé par l'incrémentation de la 
            #    colonne de travail __df_push_ver qui permet au JsCode de déclencher un selectionChanged lorsqu'il détecte une incrémentation de la 
            #    première ligne sur cette colonne. Streamlit renvoie ainsi dans response["data"] la modification, sans attendre de clic utilisateur. 

            if not df_dom.empty:
            # if isinstance(response["data"], pd.DataFrame):

                bypass_cell_edit = False

                # Si une requete de reprogrammation est en cours sur index_df on bypasse la gestion de modification de cellules
                # jusquà ce que le DOM ait enregistré la reprogrammation. Sinon une modification de valeur sur index_df est détectée 
                # et déclenche une reprogrammation inverse à celle demandée.
                reprogrammation_request = reprogrammation_request_get()
                if reprogrammation_request is not None:
                    if reprogrammation_request["idx"] == index_df:
                        matching = df_dom.index[df_dom["__index"] == index_df]
                        if not matching.empty:
                            if reprogrammation_request["jour"] == safe_int(df_dom.at[matching[0], "Date"]): # la modification de date a été prise en compte par le DOM
                                reprogrammation_request_del()
                            else:
                                bypass_cell_edit = True

                # Si une requete de modification de ligne est en cours sur index_df on bypasse la gestion de modification de cellules
                # jusquà ce que le DOM ait enregistré la modification de ligne. Sinon une modification de valeur sur index_df est détectée 
                # et déclenche une modification inverse à celle demandée.
                row_modification_request = row_modification_request_get()
                if row_modification_request is not None:
                    if row_modification_request["idx"] == index_df:
                        matching = df_dom.index[df_dom["__index"] == index_df]
                        if not matching.empty:
                            for col, val in row_modification_request["cols"].items():
                                val_dom = df_dom.at[matching[0], df_display_col_nom(col)]
                                if (pd.isna(val_dom) and pd.notna(val)) or str(val_dom) != str(val): # la modification de date a été prise en compte par le DOM
                                    bypass_cell_edit = True
                            if not bypass_cell_edit:
                                row_modification_request_del()

                if not bypass_cell_edit:
                    i, idx = get_ligne_modifiee(df_dom, st.session_state.activites_programmees_df_display_copy, columns_to_drop=work_cols)
                    if i is not None:
                        if idx == index_df: # on ne considère que les modifications sur la ligne ayant généré l'event
                            st.session_state.aggrid_activites_programmees_erreur = None
                            for col in [col for col in df_dom.columns if col not in non_editable_cols]:
                                col_df = RENOMMAGE_COLONNES_INVERSE[col] if col in RENOMMAGE_COLONNES_INVERSE else col
                                if pd.isna(df.at[idx, col_df]) and pd.isna(df_dom.at[i, col]):
                                    continue
                                if col == "Date":
                                    if df_dom.at[i, col] == "":
                                        # Déprogrammation de l'activité (Suppression de l'activité des activités programmées)
                                        undo.save()
                                        demander_selection("activites_non_programmees", idx, deselect="activites_programmees", visible_id=ligne_voisine_index(df_display, idx))
                                        activites_programmees_deprogrammer(idx)
                                        demander_selection("creneaux_disponibles", get_creneau_proche(st.session_state.get("creneaux_disponibles"), df.loc[idx])[0])
                                        st.session_state["activites_programmables_selected_row"] = df.loc[idx]
                                        st.rerun()
                                    elif pd.isna(df.at[idx, "Date"]) or df_dom.at[i, col] != str(int(df.at[idx, "Date"])):
                                        # Reprogrammation de l'activité à la date choisie
                                        jour_choisi = date_to_dateint(df_dom.at[i, col])
                                        undo.save()
                                        demander_selection("activites_programmees", idx, deselect="activites_non_programmees")
                                        activites_programmees_reprogrammer(idx, jour_choisi)
                                        demander_selection("creneaux_disponibles", get_creneau_proche(st.session_state.get("creneaux_disponibles"), df.loc[idx])[0])
                                        st.rerun()
                                else:
                                    if (pd.isna(df.at[idx, col_df]) and pd.notna(df_dom.at[i, col])) or df.at[idx, col_df] != df_dom.at[i, col]:
                                        demander_selection("activites_programmees", idx, deselect="activites_non_programmees")
                                        activites_programmees_modifier_cellule(idx, col_df, df_dom.at[i, col])
                                        demander_selection("creneaux_disponibles", get_creneau_proche(st.session_state.get("creneaux_disponibles"), df.loc[idx])[0])
                                        st.rerun()

# Menu activité à afficher dans la sidebar si click dans aggrid d'activités programmées         }
def menu_activites_programmees(index_df):

    df = st.session_state.df
    df_display = st.session_state.activites_programmees_df_display
    nom_activite = df.at[index_df, "Activite"] if  isinstance(df, pd.DataFrame) and index_df is not None and index_df in df.index else ""
    nom_activite = nom_activite.strip() if pd.notna(nom_activite) else ""

    boutons_disabled = nom_activite == "" or pd.isna(index_df) or not isinstance(df, pd.DataFrame) or (isinstance(df, pd.DataFrame) and len(df) == 0)
    activite_reservee = est_activite_reserve(df.loc[index_df]) if pd.notna(index_df) else True 
    # jours_possibles = get_jours_possibles(df, st.session_state.activites_programmees, index_df)
    jours_possibles = sorted(parse_options_date(df_display.at[index_df,"__options_date"]) - {"", df_display.at[index_df, "Date"]}, key=lambda d: date_to_dateint(d)) if index_df in df_display.index else {} 

    # Affichage du label d'activité
    afficher_nom_activite(df, index_df, nom_activite)

    # Affichage du contrôle recherche sur le Web
    afficher_bouton_web(nom_activite, disabled=boutons_disabled or est_nom_pause(nom_activite))

    # Affichage du contrôle recherche itinéraire
    afficher_bouton_itineraire(df.loc[index_df, "Lieu"] if pd.notna(index_df) and len(df) > 0 else "")

    # Affichage contrôle Supprimer
    if st.button(LABEL_BOUTON_SUPPRIMER, use_container_width=CENTRER_BOUTONS, disabled=boutons_disabled or activite_reservee, key="menu_activite_supprimer"):
        undo.save()
        demander_selection("activites_programmees", ligne_voisine_index(df_display, index_df), deselect="activites_non_programmees")
        demander_selection("creneaux_disponibles", get_creneau_proche(st.session_state.get("creneaux_disponibles"), df.loc[index_df])[0])
        st.session_state.forcer_maj_menu_activites_programmees = True
        supprimer_activite(index_df)
        # forcer_reaffichage_df("creneaux_disponibles")
        sql.sauvegarder_row(index_df)
        st.rerun()

    # Affichage contrôle Déprogrammer
    if st.button(LABEL_BOUTON_DEPROGRAMMER, use_container_width=CENTRER_BOUTONS, disabled=boutons_disabled or activite_reservee or est_nom_pause(nom_activite), key="menu_activite_deprogrammer"):
        undo.save()
        st.session_state.forcer_menu_activites_non_programmees = True
        demander_selection("activites_non_programmees", index_df, deselect="activites_programmees")
        deprogrammer_activite_programmee(index_df)
        demander_selection("creneaux_disponibles", get_creneau_proche(st.session_state.get("creneaux_disponibles"), df.loc[index_df])[0])
        st.session_state["activites_programmables_selected_row"] = df.loc[index_df]
        # forcer_reaffichage_df("creneaux_disponibles")
        sql.sauvegarder_row(index_df)
        st.rerun()

    # Affichage contrôle Reprogrammer
    if st.button(LABEL_BOUTON_REPROGRAMMER, use_container_width=True, disabled=boutons_disabled or activite_reservee or est_nom_pause(nom_activite) or not jours_possibles, key="menu_activite_programmer"):
        if "activites_programmees_jour_choisi" in st.session_state:
            jour_choisi = st.session_state.activites_programmees_jour_choisi
            undo.save()
            demander_selection("activites_programmees", index_df, deselect="activites_non_programmees")
            reprogrammation_request_set(index_df, int(jour_choisi)) # inhibe les cellValuChanged résultant de cette modification et qui inverseraient l'opération
            modifier_cellule(index_df, "Date", int(jour_choisi))
            demander_selection("creneaux_disponibles", get_creneau_proche(st.session_state.get("creneaux_disponibles"), df.loc[index_df])[0])
            sql.sauvegarder_row(index_df)
            st.rerun()
    
    # Affichage Liste des jours possibles
    jours_label = [dateint_to_str(int(x)) for x in jours_possibles] # [dateint_to_str(x) for x in jours_possibles]
    if jours_label and (not st.session_state.get("menu_activite_choix_jour_programmation") or st.session_state.menu_activite_choix_jour_programmation not in jours_label):
            st.session_state.menu_activite_choix_jour_programmation = jours_label[0]
    choix = st.selectbox("Jours possibles", jours_label, label_visibility="visible", disabled=boutons_disabled or activite_reservee or not jours_possibles, key = "menu_activite_choix_jour_programmation") 
    st.session_state.activites_programmees_jour_choisi = date_to_dateint(choix)
        
    # Affichage de l'éditeur d'activité
    if st.button(LABEL_BOUTON_EDITER, use_container_width=CENTRER_BOUTONS, disabled=boutons_disabled, key="menu_activite_bouton_editer"):
        if "editeur_activite_etat" in st.session_state:
            del st.session_state["editeur_activite_etat"]
        show_dialog_editeur_activite(df, index_df)
                               
    # Affichage du contrôle Ajouter
    afficher_bouton_nouvelle_activite(key="menu_activite_bouton_nouvelle_activite")

# Initialisation des grid_options sur la grille des activités non programmées
def init_activites_non_programmees_grid_options(df_display):

    gb = GridOptionsBuilder.from_dataframe(df_display)

    # Configuration par défaut des colonnes
    gb.configure_default_column(resizable=True)

    # Colonnes à largeur fixe
    colonnes_fixes = {"Date": 55, "Début": 55, "Fin": 55, "Durée": 55}
    for col, width in colonnes_fixes.items():
        gb.configure_column(
            col,
            filter=False,
            resizable=False,
            width=width,
            minWidth=width,
            maxWidth=width,
            flex=0,
            suppressSizeToFit=True,
        )

    # Epinglage de la colonne Date
    gb.configure_column(
        "Date",
        pinned=JsCode("'left'")
    )

    # Masquage des colonnes de travail
    work_cols = ACTIVITES_NON_PROGRAMMEES_WORK_COLS
    for col in work_cols:
        gb.configure_column(col, hide=True)

    # Colonnes editables
    non_editable_cols = ["Fin"] + work_cols
    for col in df_display.columns:
        gb.configure_column(col, editable=(col not in non_editable_cols))

    # Configuration de la colonne "Date"
    # gb.configure_column(
    #     "Date",
    #     editable=True,
    #     cellEditor="agSelectCellEditor",
    #     cellEditorParams=JsCode("""
    #         function(params) {
    #             let raw = params.data.__options_date;
    #             let values = [];

    #             try {
    #                 values = JSON.parse(raw);
    #             } catch (e) {
    #                 values = [];
    #             }

    #             return { values: values };
    #         }
    #     """),
    #     # valueParser=JS_DATE_VALUE_PARSER,
    #     # valueFormatter=JS_DATE_VALUE_FORMATTER,
    # )
    gb.configure_column(
        "Date",
        editable=True,
        cellEditor="agSelectCellEditor",
        cellEditorParams=JS_DATE_CELL_EDITOR_PARAMS,
        valueParser=JS_DATE_VALUE_PARSER,
        valueFormatter=JS_DATE_VALUE_FORMATTER,
    )

    # Configuration de l'appui long pour la recherche Web et la recherche d'itinéraire
    gb.configure_column("Activité", editable=True, cellRenderer=JS_ACTIVITE_RENDERER) #, minWidth=220)
    gb.configure_column("Lieu",     editable=True, cellRenderer=JS_LIEU_RENDERER) #, minWidth=200)

    # Colorisation 
    gb.configure_grid_options(getRowStyle= JsCode(f"""
        function(params) {{
            if (params.data.__options_date !== "[]") {{
                return {{
                    'backgroundColor': '{COULEUR_ACTIVITE_PROGRAMMABLE}'
                }}
            }}
            return null;
        }}
        """))

    # Configuration de la sélection
    gb.configure_selection(selection_mode="single", use_checkbox=False) 
    
    gb.configure_grid_options(
        getRowNodeId=JsCode("function(data) { return String(data.__uuid); }"),
        getRowId=JsCode("function(p){ return String(p.data.__uuid); }"),
        columnTypes={"textColumn": {}},  # évite l'erreur #36
        onGridReady=JS_SELECT_DESELECT_ONCE,
    )

    # Mise en page de la grille 
    gb.configure_grid_options(onFirstDataRendered=JsCode(f"""
        function(params) {{
            params.api.sizeColumnsToFit();
        }}
    """))

    grid_options = gb.build()

    # Empêche la possibilité de réorganiser les colonnes
    grid_options["suppressMovableColumns"] = True

    # Supprime le highlight de survol qui pose problème sur mobile et tablette
    grid_options["suppressRowHoverHighlight"] = True

    # Enregistre dans le contexte les paramètres nécessaires à la recherche d'itinéraire (voir JS_LIEU_xxx_RENDERER)
    grid_options["context"] = {
        "itineraire_app": st.session_state.get("itineraire_app", "Google Maps"),
        "platform": get_platform(),  # "iOS" / "Android" / "Desktop"
    }

    return grid_options

# Affiche les activités non programmées dans un tableau
def afficher_activites_non_programmees():

    df = st.session_state.get("df")
    if df is None:
        return
    
    df_display = st.session_state.get("activites_non_programmees_df_display")
    if df_display is None:
        return
    
    work_cols = ACTIVITES_NON_PROGRAMMEES_WORK_COLS
    non_editable_cols = ["Fin"] + work_cols

    # Calcul de la hauteur de l'aggrid
    nb_lignes = len(df_display)
    ligne_px = 30  # hauteur approximative d’une ligne dans AgGrid
    max_height = 250
    height = min(nb_lignes * ligne_px + 50, max_height)

    # Initialisation du compteur qui permet de savoir si l'on doit forcer le réaffichage complet de l'aggrid
    st.session_state.setdefault("activites_non_programmees_key_counter", 0)
    
    # Initialisation de la variable d'état indiquant s'il convient de bypasser la section d'édition de cellule 
    st.session_state.setdefault("activites_non_programmees_bypass_cell_edit", False)

    # Initialisation de la variable d'état contenant la requête de selection / déselection
    st.session_state.setdefault("activites_non_programmees_sel_request", copy.deepcopy(SEL_REQUEST_DEFAUT))

    # Gestion des sélections / désélections demandées via demander_selection() demander_deselection()
    # Utilise le JS code JS_SELECT_DESELECT_ONCE lequel exploite les colonnes de travail suivantes:
    # __sel_id = id de la ligne à sélectionner (None si pas de contrainte de sélection)
    # __sel_ver = version de la demande de sélection (doit être incrémentée à chaque demande)
    # __desel_id = id de la ligne devant rester visible lors de la déselection (None si aucune contrainte de visibilité lors de la désélection)
    # __desel_ver = version de la demande de désélection (doit être incrémentée à chaque demande)
    # __sel_source = information renvoyée par le DOM (event.source exposé par onRowSelected) indiquant si la source de selection est "user" ou "api" selon que la demande de sélection provient d'un click utilisateur ou d'une requête python via JsCode
    # Ces colonnes sont configurées par les fonctions utilisateur demander_selection() et demander_deselection()
    # L'information de retour __sel_source est exploitée par le mecanisme de flip flop entre grille A et grille B
    # via le champ "data" de la réponse de l'aggrid (cf. fonction afficher_activites_programmees() et afficher_activites_non_programmees())
    row = None
    selection_demandee = False
    sel_request = st.session_state.get("activites_non_programmees_sel_request")
    if sel_request["sel"]["pending"]:
        if sel_request["sel"]["id"] is not None:
            reqid = sel_request["sel"]["id"]
            # tracer.log(f"Traitement de la requête de sélection {sel_request["sel"]["id"]} {sel_request["sel"]["ver"]}")
            df_display["__sel_id"] = get_uuid(df_display, reqid)
            df_display["__sel_ver"] = sel_request["sel"]["ver"]
            if reqid in df_display.index: 
                row = df_display.loc[reqid]
                # tracer.log(f"row = df_display.loc[{reqid}]")
            selection_demandee = True
        st.session_state.activites_non_programmees_sel_request["sel"]["pending"] = False

    deselection_demandee = False
    if sel_request["desel"]["pending"]:
        # tracer.log(f"Traitement de la requête de desélection {sel_request["desel"]["ver"]}")
        df_display["__desel_ver"] = sel_request["desel"]["ver"]
        df_display["__desel_id"] = get_uuid(df_display, sel_request["desel"]["id"]) # id visible après déselection, None si pas de contrainte de visibilité
        df_display["__sel_id"] = None
        deselection_demandee = True
        st.session_state.activites_non_programmees_sel_request["desel"]["pending"] = False

    # if len(df_display) > 0:
    #     tracer.log(f"df_display['__sel_id'] {df_display.iloc[0]["__sel_id"]} df_display['__sel_ver'] {df_display.iloc[0]["__sel_ver"]} df_display['__desel_ver'] {df_display.iloc[0]["__desel_ver"]}")

    grid_options = init_activites_non_programmees_grid_options(df_display)

    # Affichage
    with st.expander("**Activités non programmées**", expanded=True):
        response = AgGrid(
            df_display,
            gridOptions=grid_options,
            allow_unsafe_jscode=True,
            height=height,
            reload_data=True,
            data_return_mode=DataReturnMode.AS_INPUT,
            update_mode=(GridUpdateMode.MODEL_CHANGED | GridUpdateMode.VALUE_CHANGED
                        | GridUpdateMode.SELECTION_CHANGED),
            key=f"Activités non programmées {st.session_state.activites_non_programmees_key_counter}",  # incrémentation de la clef permet de forcer le reaffichage
        )

        # Affichage de l'erreur renvoyée par le précédent run
        erreur = st.session_state.get("aggrid_activites_non_programmees_erreur") 
        if erreur is not None:
            st.error(erreur)

        event_data = response.get("event_data")
        event_type = event_data["type"] if isinstance(event_data, dict) else None
        tracer.log(f"event {event_type}", types=["gen", "event"])

        # Pas d'event aggrid à traiter si event_type is None (i.e. le script python est appelé pour autre chose qu'un event aggrid)
        if event_type is None:
            if len(df_display) == 0:
                if st.session_state.menu_activites["menu"] == "menu_activites_non_programmees":
                    st.session_state.menu_activites = {
                        "menu": "menu_activites_non_programmees",
                        "index_df": None
                    }
            return
        
        # Récupération du retour grille __sel_source
        # Cette information est passée à la valeur "user" par le JsCode JS_SELECT_DESELECT_ONCE si le cellValueChanged provient d'un click utilisateur.
        # Elle permet de n'effectuer les traitements de cellValueChanged que sur les seuls évènements utilisateurs et de bypasser ceux provenant d'une
        # demande de sélection programmée via demander_selection().
        sel_source = "unknown"
        try:
            df_dom = pd.DataFrame(response["data"]) if "data" in response and isinstance(response["data"], pd.DataFrame) else pd.DataFrame()  
        except:
            df_dom = pd.DataFrame() 
        if not df_dom.empty:
            first_row = df_dom.iloc[0]
            sel_source = (first_row.get("__sel_source") or "api") # 'user' ou 'api'
            tracer.log(f"sel_source {sel_source}", types=["sel_source"])

        # Récupération de la ligne sélectionnée
        selected_rows = response["selected_rows"] if "selected_rows" in response else None
        row = None
        if not selection_demandee:
            if isinstance(selected_rows, pd.DataFrame) and not selected_rows.empty:
                # tracer.log("row = selected_rows.iloc[0]")
                row = selected_rows.iloc[0] 
            elif isinstance(selected_rows, list) and len(selected_rows) > 0:
                # tracer.log("row = selected_rows[0]")
                row = selected_rows[0]

        # 🟡 Traitement si ligne sélectionnée et index correspondant non vide
        if row is not None:

            # Récupération de l'index de ligne sélectionnée
            index_df = row["__index"]

            # Evènement de type "selectionChanged"
            if event_type == "selectionChanged":
                # tracer.log(f"Selected row {selected_rows.iloc[0]["__index"] if isinstance(selected_rows, pd.DataFrame) and not selected_rows.empty else (selected_rows[0]["__index"] if isinstance(selected_rows, list) and len(selected_rows) > 0 else None)}")
                if index_df != st.session_state.activites_non_programmees_sel_request["sel"]["id"] and not deselection_demandee and sel_source == "user":
                    # tracer.log(f"***activites_non_programmees_sel_request[id] de {st.session_state.activites_non_programmees_sel_request["sel"]["id"]} à {index_df}")
                    st.session_state.activites_non_programmees_sel_request["sel"]["id"] = index_df
                    # tracer.log(f"***demander_deselection activites_programmees")
                    demander_deselection("activites_programmees")

                    # time.sleep(0.05) # Hack défensif pour éviter les erreurs Connection error Failed to process a Websocket message Cached ForwardMsg MISS

                    if not st.session_state.forcer_menu_activites_programmees:
                        st.session_state.editeur_activite_idx = index_df
                        st.session_state.menu_activites = {
                            "menu": "menu_activites_non_programmees",
                            "index_df": index_df
                        }
                    st.rerun()
                else:
                    if st.session_state.forcer_menu_activites_non_programmees or st.session_state.forcer_maj_menu_activites_non_programmees:
                        st.session_state.editeur_activite_idx = index_df
                        st.session_state.menu_activites = {
                            "menu": "menu_activites_non_programmees",
                            "index_df": index_df
                        }
                        st.session_state.forcer_maj_menu_activites_non_programmees = False

            # Gestion des modifications de cellules
            # Attention : la modification de cellule uniquement sur "cellValueChanged" n'est pas suffisante, car lorsque l'on valide la modification
            # de cellule en cliquant sur une autre ligne, on reçoit un event de type "selectionChanged" et non "cellValueChanged". Mais cela implique 
            # que toute modification programmée de cellule (via l'éditeur d'activité ou les boutons de programmation) va engendrée un écart entre le 
            # df_display modifié par programmation et le df_dom revenant du DOM, ce qui, via le code ci-dessous, va déclencher une modification inverse 
            # à celle souhaitée. Pour eviter cela il faut :
            # 1. Mettre en place un mécanisme de requête de modification qui bypasse la modification de cellule tant que le DOM n'a pas enregistré les 
            #    modifications demandées via le df_display (voir reprogrammation_request et row_modification_request).
            # 2. S'assurer que le DOM renvoie bien via response["data"] les modifications enregistrées. Ceci est réalisé par l'incrémentation de la 
            #    colonne de travail __df_push_ver qui permet au JsCode de déclencher un selectionChanged lorsqu'il détecte une incrémentation de la 
            #    première ligne sur cette colonne. Streamlit renvoie ainsi dans response["data"] la modification, sans attendre de clic utilisateur. 
            if not df_dom.empty:
            # if isinstance(response["data"], pd.DataFrame):

                bypass_cell_edit = False

                # Si une requete de modification de ligne est en cours sur index_df on bypasse la gestion de modification de cellules
                # jusquà ce que le DOM ait enregistré la modification de ligne. Sinon une modification de valeur sur index_df est détectée 
                # et déclenche une modification inverse à celle demandée.
                row_modification_request = row_modification_request_get()
                if row_modification_request is not None:
                    if row_modification_request["idx"] == index_df:
                        matching = df_dom.index[df_dom["__index"] == index_df]
                        if not matching.empty:
                            for col, val in row_modification_request["cols"].items():
                                val_dom = df_dom.at[matching[0], df_display_col_nom(col)]
                                if (pd.isna(val_dom) and pd.notna(val)) or str(val_dom) != str(val): # la modification de date a été prise en compte par le DOM
                                    bypass_cell_edit = True
                            if not bypass_cell_edit:
                                row_modification_request_del()

                if not bypass_cell_edit:
                    i, idx = get_ligne_modifiee(df_dom, st.session_state.activites_non_programmees_df_display_copy, columns_to_drop=work_cols)
                    if i is not None:
                        if idx == index_df: # on ne considère que les modifications sur la ligne ayant généré l'event
                            st.session_state.aggrid_activites_non_programmees_erreur = None
                            for col in [col for col in df_dom.columns if col not in non_editable_cols]:
                                col_df = RENOMMAGE_COLONNES_INVERSE[col] if col in RENOMMAGE_COLONNES_INVERSE else col
                                if pd.isna(df.at[idx, col_df]) and pd.isna(df_dom.at[i, col]):
                                    continue
                                if col == "Date":
                                    if df_dom.at[i, col] != "":
                                        # Programmation de l'activité à la date choisie
                                        jour_choisi = date_to_dateint(df_dom.at[i, col])
                                        undo.save()
                                        demander_selection("activites_programmees", idx, deselect="activites_non_programmees")
                                        activites_non_programmees_programmer(idx, jour_choisi)
                                        demander_selection("creneaux_disponibles", get_creneau_proche(st.session_state.get("creneaux_disponibles"), df.loc[idx])[0])
                                        st.rerun()
                                else:
                                    if (pd.isna(df.at[idx, col_df]) and pd.notna(df_dom.at[i, col])) or df.at[idx, col_df] != df_dom.at[i, col]:
                                        demander_selection("activites_non_programmees", idx, deselect="activites_programmees")
                                        activites_non_programmees_modifier_cellule(idx, col_df, df_dom.at[i, col])
                                        demander_selection("creneaux_disponibles", get_creneau_proche(st.session_state.get("creneaux_disponibles"), df.loc[idx])[0])
                                        st.rerun()

        elif len(df_display) == 0:
            if st.session_state.menu_activites["menu"] == "menu_activites_non_programmees":
                st.session_state.menu_activites = {
                    "menu": "menu_activites_non_programmees",
                    "index_df": None
                }

# Menu activité à afficher dans la sidebar si click dans aggrid d'activités non programmées         }
def menu_activites_non_programmees(index_df):

    df = st.session_state.df
    df_display = st.session_state.activites_non_programmees_df_display
    nom_activite = df.at[index_df, "Activite"] if  isinstance(df, pd.DataFrame) and index_df is not None and index_df in df.index else ""
    nom_activite = nom_activite.strip() if pd.notna(nom_activite) else ""

    boutons_disabled = nom_activite == "" or pd.isna(index_df) or not isinstance(df, pd.DataFrame) or (isinstance(df, pd.DataFrame) and len(df) == 0)
    # jours_possibles = get_jours_possibles(df, st.session_state.activites_programmees, index_df)
    jours_possibles = sorted(parse_options_date(df_display.at[index_df,"__options_date"]) - {"", df_display.at[index_df, "Date"]}, key=lambda d: date_to_dateint(d)) if index_df in df_display.index else {} 

    # Affichage du label d'activité
    afficher_nom_activite(df, index_df, nom_activite)

    # Affichage du contrôle recherche sur le Web
    afficher_bouton_web(nom_activite, disabled=boutons_disabled or est_nom_pause(nom_activite))

    # Affichage du contrôle recherche itinéraire
    afficher_bouton_itineraire(df.loc[index_df, "Lieu"] if pd.notna(index_df) and len(df) > 0 else "")

    # Affichage contrôle Supprimer
    if st.button(LABEL_BOUTON_SUPPRIMER, use_container_width=CENTRER_BOUTONS, disabled=boutons_disabled, key="menu_activite_supprimer"):
        undo.save()
        demander_selection("activites_non_programmees", ligne_voisine_index(df_display, index_df), deselect="activites_programmees")
        demander_selection("creneaux_disponibles", get_creneau_proche(st.session_state.get("creneaux_disponibles"), df.loc[index_df])[0])
        st.session_state.forcer_maj_menu_activites_non_programmees = True
        supprimer_activite(index_df)
        # forcer_reaffichage_df("activites_programmable_dans_creneau_selectionne")
        sql.sauvegarder_row(index_df)
        st.rerun()

    # Affichage contrôle Deprogrammer
    st.button(LABEL_BOUTON_DEPROGRAMMER, use_container_width=CENTRER_BOUTONS, disabled=True, key="menu_activite_deprogrammer")

    # Affichage contrôle Programmer
    if st.button(LABEL_BOUTON_PROGRAMMER, use_container_width=CENTRER_BOUTONS, disabled=boutons_disabled or not jours_possibles, key="menu_activite_programmer"):
        if "activites_non_programmees_jour_choisi" in st.session_state:
            jour_choisi = st.session_state.activites_non_programmees_jour_choisi
            undo.save()
            st.session_state.forcer_menu_activites_programmees = True
            demander_selection("activites_programmees", index_df, deselect="activites_non_programmees")
            modifier_cellule(index_df, "Date", int(jour_choisi))
            demander_selection("creneaux_disponibles", get_creneau_proche(st.session_state.get("creneaux_disponibles"), df.loc[index_df])[0])
            # forcer_reaffichage_df("creneaux_disponibles")
            sql.sauvegarder_row(index_df)
            st.rerun()

    # Affichage Liste des jours possibles
    jours_label = [dateint_to_str(int(x)) for x in jours_possibles] # [dateint_to_str(x) for x in jours_possibles]
    if jours_label and (not st.session_state.get("menu_activite_choix_jour_programmation") or st.session_state.menu_activite_choix_jour_programmation not in jours_label):
            st.session_state.menu_activite_choix_jour_programmation = jours_label[0]
    choix = st.selectbox("Jours possibles", jours_label, label_visibility="visible", disabled=boutons_disabled or not jours_possibles, key = "menu_activite_choix_jour_programmation") # , width=90
    st.session_state.activites_non_programmees_jour_choisi = date_to_dateint(choix)
        
    # Affichage de l'éditeur d'activité
    if st.button(LABEL_BOUTON_EDITER, use_container_width=CENTRER_BOUTONS, disabled=boutons_disabled,  key="menu_activite_bouton_editer"):
        if "editeur_activite_etat" in st.session_state:
            del st.session_state["editeur_activite_etat"]
        show_dialog_editeur_activite(df, index_df)

    # Affichage contrôle Ajouter
    afficher_bouton_nouvelle_activite(key="menu_activite_bouton_nouvelle_activite")

# Affichage de l'éditeur d'activité en mode modal
@st.dialog("Editeur d'activité")
def show_dialog_editeur_activite(df, index_df):
    afficher_nom_activite(df, index_df, afficher_label=False)
    afficher_editeur_activite(df, index_df)

# Affichage de l'éditeur d'activité
def afficher_editeur_activite(df, index_df=None, key="editeur_activite"):

    def enregistrer_modification_dans_row(df, row, colonne_df, valeur_courante, nouvelle_valeur):

        erreur = None
        if (pd.isna(valeur_courante) and (pd.notna(nouvelle_valeur) and str(nouvelle_valeur) != "")) or (pd.notna(valeur_courante) and str(valeur_courante) != str(nouvelle_valeur)):
        
            erreur = affecter_valeur_row(row, colonne_df, nouvelle_valeur)

            if erreur is not None:
                st.error(erreur)
            else:
                try:
                    if  colonne_df != "Lien Web":
                        if colonne_df == "Date":
                            row[colonne_df] = date_to_dateint(row[colonne_df])
                        elif ptypes.is_numeric_dtype(df[colonne_df]) and not ptypes.is_numeric_dtype(row[colonne_df]):
                            if "." not in row[colonne_df] and "," not in row[colonne_df] and "e" not in row[colonne_df].lower():
                                row[colonne_df] = int(row[colonne_df])
                            else:
                                row[colonne_df] = float(row[colonne_df])
                except Exception as e:
                    erreur = f"⛔ Format numérique attendu pour cette colonne"
                    st.error(erreur)
                if (pd.isna(nouvelle_valeur) and not pd.isna(valeur_courante)) or (not pd.isna(nouvelle_valeur) and pd.isna(valeur_courante)) or nouvelle_valeur != valeur_courante:
                    if colonne_df == "Lien Web":
                        st.session_state.editeur_activite_etat["lien_modif"] = True
                    else:
                        st.session_state.editeur_activite_etat["col_modif"].append(colonne_df)
                        if est_activite_programmee(row):
                            if colonne_df in ["Debut", "Duree", "Activité"]:
                                st.session_state.editeur_activite_etat["forcer_reaffichage_creneaux_disponibles"] = True
                        elif est_activite_non_programmee(row):
                            st.session_state.editeur_activite_etat["forcer_reaffichage_activites_programmables"] = True
        return erreur
                
    # Rien à faire sur df vide
    if len(df) <= 0:
        return
    
    if index_df is None:
        if "editeur_activite_idx" in st.session_state:
            index_df = st.session_state.editeur_activite_idx 
    
    if index_df is not None:

        input_text_key_counter_key = key + "input_text_key_counter"
        st.session_state.setdefault(input_text_key_counter_key, 0)
        input_text_key_counter = st.session_state.get(input_text_key_counter_key)
        if "editeur_activite_etat" not in st.session_state:
            input_text_key_counter += 1
            st.session_state[input_text_key_counter_key] = input_text_key_counter

        st.session_state.setdefault("editeur_activite_etat", {
            "row": df.loc[index_df].copy(),
            "colonne_courante": None,
            "nouvelle_valeur": None,
            "col_modif": [],
            "forcer_reaffichage_activites_programmees": False,
            "forcer_reaffichage_activites_non_programmees": False,
            "forcer_reaffichage_creneaux_disponibles": False,
            "forcer_reaffichage_activites_programmables": False,
            "erreur": None,
        })

        row = st.session_state.editeur_activite_etat["row"]

        if est_activite_reserve(row):
            colonnes_editables = [col for col in df.columns if col not in ["Date", "Fin", "Debut_dt", "Duree_dt", "Debut", "Duree", "__uuid", "__options_date"]]
        else:
            colonnes_editables = [col for col in df.columns if col not in ["Date", "Fin", "Debut_dt", "Duree_dt", "__uuid", "__options_date"]]

        # Traitement de l'accentuation
        colonnes_editables_avec_accents = [RENOMMAGE_COLONNES.get(col, col) for col in colonnes_editables]
        
        colonne = st.selectbox("⚙️ Colonne", colonnes_editables_avec_accents, key=key+"_selectbox_choix_colonne")
        colonne_df = RENOMMAGE_COLONNES_INVERSE[colonne] if colonne in RENOMMAGE_COLONNES_INVERSE else colonne

        colonne_rerun_pred = st.session_state.editeur_activite_etat.get("colonne_courante")
        if colonne_rerun_pred is None or  colonne_rerun_pred != colonne_df:
            st.session_state.editeur_activite_etat["colonne_courante"] = colonne_df

        if colonne_df == "Date":
            valeur_courante = dateint_to_str(row[colonne_df])
        else:
            valeur_courante = row[colonne_df]

        st.session_state.editeur_activite_etat["nouvelle_valeur"] = st.text_input(f"✏️ Valeur", "" if pd.isna(valeur_courante) else str(valeur_courante), key=key+str(input_text_key_counter)) 
        erreur = enregistrer_modification_dans_row(df, row, colonne_df, row[colonne_df], st.session_state.editeur_activite_etat.get("nouvelle_valeur"))

        if st.button(LABEL_BOUTON_VALIDER, use_container_width=CENTRER_BOUTONS):
            if not erreur and st.session_state.editeur_activite_etat["col_modif"]:
                undo.save()
                try:
                    if st.session_state.editeur_activite_etat["col_modif"]:
                        cols = {}
                        for col in st.session_state.editeur_activite_etat["col_modif"]:
                            cols[col] = row[col]
                            modifier_cellule(index_df, col, row[col])

                        if st.session_state.editeur_activite_etat["forcer_reaffichage_activites_programmees"]:
                            forcer_reaffichage_activites_programmees()
                        if st.session_state.editeur_activite_etat["forcer_reaffichage_activites_non_programmees"]:
                            forcer_reaffichage_activites_non_programmees()
                            
                        if st.session_state.editeur_activite_etat["forcer_reaffichage_creneaux_disponibles"]:
                            # forcer_reaffichage_df("creneaux_disponibles")
                            pass
                        if st.session_state.editeur_activite_etat["forcer_reaffichage_activites_programmables"]:
                            # forcer_reaffichage_df("activites_programmables")
                            pass

                        # Mise en attente du code de traitement des cellValueChanged utilisateur tant que le DOM n'a pas pris en compte les modifs
                        row_modification_request_set(index_df, cols)
                        if est_activite_programmee(row): signaler_df_push("activites_programmees")
                        if est_activite_non_programmee(row): signaler_df_push("activites_non_programmees")
                        
                        sql.sauvegarder_row(index_df)

                    st.rerun()
                except Exception as e:
                    st.error(f"⛔ {e}")
                    undo.undo()

            else:
                st.rerun()
                    
# Programme une activité en fonction des créneaux possibles
def afficher_creneaux_disponibles():

    def on_toggle_pauses():
        st.session_state.traiter_pauses_change = True
        st.session_state.traiter_pauses = st.session_state.traiter_pauses_cb
        maj_creneaux_disponibles()
        sql.sauvegarder_param("traiter_pauses")
        st.session_state.creneaux_disponibles_choix_activite = None

    df = st.session_state.get("df")
    if df is None or len(df) <= 0:
        return
    
    with st.expander("**Créneaux disponibles**", expanded=True):

        # Gestion du flag de traitement des pauses
        traiter_pauses = st.checkbox("Tenir compte des pauses", value=st.session_state.get("traiter_pauses", False), key="traiter_pauses_cb", on_change=on_toggle_pauses)  
        traiter_pauses_change = st.session_state.get("traiter_pauses_change", False)
        st.session_state["traiter_pauses_change"] = False

        creneaux_disponibles = st.session_state.get("creneaux_disponibles")
        if creneaux_disponibles is None or creneaux_disponibles.empty:
            return 

        proposables = []

        st.session_state.creneaux_disponibles_choix_activite = None

        # Récupération du creneau enregistré au run précédent
        choix_creneau_pred = st.session_state["creneaux_disponibles_selected_row"] if "creneaux_disponibles_selected_row" in st.session_state else None

        # Affichage de la grille des créneaux disponibles
        choix_creneau, *_ = afficher_df(
            "Créneaux disponibles", 
            creneaux_disponibles, 
            header_names={"Debut": "Début"},
            fixed_columns={"Date": 55, "Debut": 55, "Fin": 55}, 
            hide=["__type_creneau", "__index", "__uuid"], 
            key="creneaux_disponibles", 
            hide_label=True, 
            colorisation=True)

        if choix_creneau is not None:

            # Choix d'une activité à programmer dans le creneau choisi
            if (choix_creneau_pred is not None and choix_creneau_pred["__uuid"] != choix_creneau["__uuid"]) or \
                traiter_pauses_change or \
                "activites_programmables" not in st.session_state:

                if choix_creneau_pred is not None and choix_creneau_pred["__uuid"] != choix_creneau["__uuid"]:
                    # forcer_reaffichage_df("activites_programmables")
                    pass
            
                proposables = get_proposables(choix_creneau, traiter_pauses)

                st.session_state.activites_programmables = proposables

                # Resélection automatique de l'activité précédemment séléectionnée si elle existe dans la nouvelle liste de proposables
                st.session_state.setdefault("activites_programmables_select_auto", True)
                if st.session_state["activites_programmables_select_auto"]:
                    current_selected_row = st.session_state.get("activites_programmables_selected_row")
                    current_selected_row_idx = get_index_from_uuid(proposables, current_selected_row["__uuid"]) if current_selected_row is not None else None
                    current_selected_row_idx = current_selected_row_idx if current_selected_row_idx is not None else proposables.index[0] if isinstance(proposables, pd.DataFrame) else None
                    demander_selection("activites_programmables", current_selected_row_idx)
                else:
                    st.session_state["activites_programmables_select_auto"] = True
            else: 
                proposables = st.session_state.get("activites_programmables", [])
        else:
            st.session_state.activites_programmables = None

    if isinstance(proposables, pd.DataFrame) and not proposables.empty:
        with st.expander("**Activités programmables**", expanded=True):
                date_ref = int(choix_creneau["Date"]) # date_ref doit être en int !
                st.markdown(f"Sur le créneau du {int(date_ref)} de {choix_creneau["Debut"]} à {choix_creneau["Fin"]}")

                activite, *_ = afficher_df(
                    "Activités programmables", 
                    proposables, 
                    header_names={"Debut": "Début", "Duree": "Durée", "Activite": "Activité", "Relache": "Relâche", "Priorite": "Prio", "Reserve": "Réservé"},
                    fixed_columns={"Date": 55, "Debut": 55, "Fin": 55, "Duree": 55}, 
                    hide=["__type_activite", "__index", "__options_date", "__uuid"], 
                    key="activites_programmables", 
                    hide_label=True, 
                    colorisation=True,
                    cell_renderers = [
                        {"col": "Activite", "renderer": JS_ACTIVITE_RENDERER}, 
                    ],
                )

                st.markdown(f"{activite["Activite"]} le {activite["Date"]} à {activite["Debut"]}" if activite is not None else "Aucune activité sélectionnée")

                # Gestion du bouton Programmer
                if st.button(LABEL_BOUTON_PROGRAMMER, disabled=activite is None, key="PagePrincipaleProgrammerParCréneau"):
                    if activite is not None:
                        st.session_state.forcer_menu_activites_programmees = True
                        programmer_activite_non_programmee(date_ref, activite)

# Signale au DOM une modification de df_display en incrémentant la première ligne de la colonne __df_push_ver.
# Cette incrémentation est captée par le JsCode JS_SELECT_DESELECT_ONCE, lequel declenche un selectionChanged de type "api"
# qui permet à Streamlit de renvoyer la prise en compte des modifications du df_display via response["data"] sans attendre de clic utilisateur.
def signaler_df_push(grid_name):
    df_display = st.session_state.get(grid_name + "_df_display")
    if df_display is not None:
        df_display.loc[df_display.index[0], "__df_push_ver"] = int(df_display.iloc[0]["__df_push_ver"] or 0) + 1

# Affichage des contrôles d'édition
def afficher_controles_edition():
    if st.button(LABEL_BOUTON_DEFAIRE, 
        disabled=not st.session_state.get("historique_undo"), 
        use_container_width=CENTRER_BOUTONS, 
        key="undo_btn") and st.session_state.historique_undo:
        undo.undo()
    if st.button(LABEL_BOUTON_REFAIRE, 
        disabled=not st.session_state.get("historique_redo"), 
        use_container_width=CENTRER_BOUTONS, 
        key="redo_btn") and st.session_state.historique_redo:
        undo.redo()

# Affichage des choix généraux
def afficher_infos_generales():

    df = st.session_state.get("df")
    if df is None:
        return
    
    with st.expander("ℹ️ Infos"):
        # Vérification de l'
        afficher_aide()        
        
        # Vérification de cohérence des informations du df
        verifier_coherence(df) 

        # Vérification de cohérence des informations du df
        afficher_periode_programmation()

        # Affichage des paramètres
        afficher_parametres()

# Affiche le nom d'activité
def afficher_nom_activite(df, index_df, nom_activite=None, afficher_label=True):

    # afficher_label = False if not st.session_state.sidebar_menus else afficher_label
    
    if index_df is not None:
        row = df.loc[index_df]
        if nom_activite == None:
            nom_activite = row["Activite"].strip()
        if est_activite_programmee(row):
            label_activite = f"Le {dateint_to_str(row["Date"])} de {row["Debut"]} à {row["Fin"]}"
            if est_activite_reserve(row):
                st_info_avec_label(label_activite, nom_activite, afficher_label=afficher_label, color="red")
            else:
                st_info_avec_label(label_activite, nom_activite, afficher_label=afficher_label)
        else:
            label_activite = f"De {row["Debut"]} à {row["Fin"]}"
            st_info_avec_label(label_activite, nom_activite, afficher_label=afficher_label)
    else:
        if nom_activite == None:
            nom_activite = ""
        label_activite = "De ..h.. à ..h.."
        st_info_avec_label(label_activite, nom_activite, afficher_label=afficher_label)
    
# Affiche un nom d'activité clickable qui switche le menu d'activités alternatif (sert en mode MODE_ACTIVITE_UNIQUE)
def afficher_nom_activite_clickable(df, index_df, nom_activite=None, afficher_label=True):

    hit = False
    key = "nom_activite_clickable" # if st.session_state.sidebar_menus else None
    # afficher_label = False if not st.session_state.sidebar_menus else afficher_label
    activite_programmee = False

    if index_df is not None:
        row = df.loc[index_df]
        activite_reservee = est_activite_reserve(row)
        activite_programmee = est_activite_programmee(row)

        # Injecte le CSS permettent de styler le primary button affiché par st_info_avec_label avec param key 
        injecter_css_pour_primary_buttons("error" if activite_reservee else "info")

        if nom_activite == None:
            nom_activite = row["Activite"].strip()
        if est_activite_programmee(row):
            label_activite = f"Le {int(row["Date"])} de {row["Debut"]} à {row["Fin"]}"
            if activite_reservee:
                hit = st_info_avec_label(label_activite, nom_activite, key, afficher_label=afficher_label, color="red")
            else:
                hit = st_info_avec_label(label_activite, nom_activite, key, afficher_label=afficher_label)
        else:
            label_activite = f"De {row["Debut"]} à {row["Fin"]}"
            hit = st_info_avec_label(label_activite, nom_activite, key, afficher_label=afficher_label)
    else:
        if nom_activite == None:
            nom_activite = ""
        label_activite = "De ..h.. à ..h.."

        # Injecte le CSS permettent de styler le primary button affiché par st_info_avec_label avec param key 
        injecter_css_pour_primary_buttons("info")
        hit = st_info_avec_label(label_activite, nom_activite, key, afficher_label=afficher_label)
    
    if hit:
        if activite_programmee:
            new_index_df = st.session_state.activites_non_programmees_sel_request["sel"]["id"] #_selected_row
            if new_index_df is not None:
                st.session_state.menu_activites = {
                    "menu": "menu_activites_non_programmees",
                    "index_df": new_index_df
                }
                demander_selection("activites_non_programmees", new_index_df, deselect="activites_programmees")
        else:
            new_index_df = st.session_state.activites_programmees_sel_request["sel"]["id"] #_selected_row
            if new_index_df is not None:
                st.session_state.menu_activites = {
                    "menu": "menu_activites_programmees",
                    "index_df": new_index_df
                }
                demander_selection("activites_programmees", new_index_df, deselect="activites_non_programmees")
        st.rerun()

# Affichage du status du GS Worker
def afficher_worker_status_detail():
    st.sidebar.subheader("Google ")
    s = wk.get_sync_status()
    col1, col2 = st.sidebar.columns(2)
    col1.metric("Alive", "✅" if s.get("alive") else "❌")
    col2.metric("Pending", s.get("pending", 0))
    st.sidebar.caption(f"Last OK: {s.get('last_ok')}")
    if s.get("last_err"):
        st.sidebar.error(s["last_err"])
    if st.sidebar.button("Ping worker"):
        ok = wk.enqueue_noop()
        st.sidebar.write("Enqueue:", "ok" if ok else "queue None")

    # ###################################################################################################################
    # A BANNIR ABSOLUMENT CAR streamlit_autorefresh INTERROMPT TOUT TRAITEMENT QUI N'EST PAS MIS EN SECTION CRITIQUE ET
    # POUR CEUX QUI LE SONT EMPECHE QU'ILS SE TERMINENT SI LA PLUS LONGUE DE LEURS ETAPES EST PLUS LONGUE QUE LE TIMEOUT 
    # D'AUTOREFRESH, D'OU FIGEAGE D'UI ET EVENTUELLE PERTE DE COHERENCE DU CONTEXTE;
    # ####################################################################################################################
    # # Auto-refresh tant qu’il y a du travail
    # from streamlit_autorefresh import st_autorefresh
    # if s.get("pending", 0) > 0:
    #     st_autorefresh(interval=1000, key="gsync_poll")

# Affichage du status du GS Worker (version discrète)
def afficher_worker_status(with_pending=True):
    s = wk.get_sync_status() if "wk" in globals() else {}
    alive   = bool(s.get("alive"))
    pending = int(s.get("pending", 0))
    last_ok = s.get("last_ok")
    last_err = s.get("last_err")

    color = "#16a34a" if alive else "#ef4444"   # vert / rouge
    title = "OK" if alive else "Hors ligne"

    if with_pending:
        if pending > 0:
            title = f"Sync en cours… ({pending})"
        if last_err:
            title = f"Erreur: {last_err}"

        html = f"""
        <div style="
            display:flex;align-items:center;gap:.5rem;
            font-size:0.90rem; line-height:1.2; margin:.25rem 0 .25rem .1rem;">
        <span title="{title}" style="color:{color};font-size:1rem;">●</span>
        <span style="opacity:.9;">Google&nbsp;Sheet</span>
        {"<span style='margin-left:auto;opacity:.6;font-variant-numeric:tabular-nums;'>"+str(pending)+"</span>" if pending>0 else ""}
        </div>
        """
    else:
        html = f"""
        <div style="
            display:flex;align-items:center;gap:.5rem;
            font-size:0.90rem; line-height:1.2; margin:.25rem 0 .25rem .1rem;">
        <span title="{title}" style="color:{color};font-size:1rem;">●</span>
        <span style="opacity:.9;">{get_user_id()}</span>
        </div>
        """
    st.sidebar.markdown(html, unsafe_allow_html=True)

    # ###################################################################################################################
    # A BANNIR ABSOLUMENT CAR streamlit_autorefresh INTERROMPT TOUT TRAITEMENT QUI N'EST PAS MIS EN SECTION CRITIQUE ET
    # POUR CEUX QUI LE SONT EMPECHE QU'ILS SE TERMINENT SI LA PLUS LONGUE DE LEURS ETAPES EST PLUS LONGUE QUE LE TIMEOUT 
    # D'AUTOREFRESH, D'OU FIGEAGE D'UI ET EVENTUELLE PERTE DE COHERENCE DU CONTEXTE;
    # ####################################################################################################################
    # # Auto-refresh UNIQUEMENT si des tâches sont en attente
    # if pending > 0:
    #     try:
    #         from streamlit_autorefresh import st_autorefresh
    #         st_autorefresh(interval=1000, key="gsync_poll")
    #     except Exception:
    #         pass    

# Affichage du user_id
def afficher_user_id():
    st.sidebar.write(f"user_id: {get_user_id()}")

# Affichage de la la sidebar min avec menus fichier et edition 
# (le reste est affiché dans d'affichage de données en fonction du contexte)
def afficher_sidebar():

    st.sidebar.title("Menu principal")

    with st.sidebar.expander("Fichier"):
        creer_nouveau_contexte()
        charger_contexte_depuis_fichier()
        sauvegarder_contexte()

    with st.sidebar.expander("Edition"):
        afficher_controles_edition()

# Affichage des menus complémentaitres de la sidebar
def afficher_sidebar_menus():
    afficher_menu_activite()
    afficher_menu_ca()
    afficher_worker_status()

# Affichage du menu activité de la sidebar
def afficher_menu_activite():

    def clipboard_on_change():
        st.session_state.zone_collage = st.session_state["clipboard"]

    df = st.session_state.get("df")
    if df is None:
        return
    
    if est_contexte_valide():
        with st.sidebar.expander("Activités", expanded=True):
            if "menu_activites" in st.session_state and isinstance(st.session_state.menu_activites, dict):
                if st.session_state.menu_activites["menu"] == "menu_activites_programmees":
                    menu_activites_programmees(
                        st.session_state.menu_activites["index_df"]
                    )

                elif st.session_state.menu_activites["menu"] == "menu_activites_non_programmees":
                    menu_activites_non_programmees(
                        st.session_state.menu_activites["index_df"]
                    )

                # Ajout de la zone de collage
                st.session_state.zone_collage = st.text_area(
                    "Clipboard", 
                    height=120, 
                    width="stretch", 
                    key="clipboard", 
                    placeholder="Collez ici le texte à utiliser pour créer une nouvelle activité",
                    on_change=clipboard_on_change,
                )

        # Désactivation des flags de forçage de menu activités
        if st.session_state.forcer_menu_activites_programmees and st.session_state.menu_activites["menu"] == "menu_activites_programmees":
            st.session_state.forcer_menu_activites_programmees = False
        if st.session_state.forcer_menu_activites_non_programmees and st.session_state.menu_activites["menu"] == "menu_activites_non_programmees":
            st.session_state.forcer_menu_activites_non_programmees = False

# Affichage du carnet d'adresses
def afficher_ca():
    ca = st.session_state.get("ca")
    if ca is not None:
        with st.expander("**Carnet d'adresses**", expanded=True):
            st.session_state.setdefault("ca_display", ca.copy())
            ca_display = st.session_state.get("ca_display")
            adresse_selectionnee, idx_modifie = afficher_df(
                "Carnet d'adresses", 
                ca_display, 
                hide=["__uuid"], 
                editable=["Nom", "Adresse", "Tel", "Web"],
                key="carnet_adresses", 
                hide_label=True,
                cell_renderers = [
                    {"col": "Tel", "renderer": JS_TEL_ICON_RENDERER},
                    {"col": "Web", "renderer": JS_WEB_ICON_RENDERER},
                ],
            )

            tracer.log(f"idx_modifie: {idx_modifie}")

            grid_has_changed = idx_modifie is not None
            st.session_state.ca_adresse_selectionnee = adresse_selectionnee

            if grid_has_changed and isinstance(adresse_selectionnee, pd.Series):
                idx_ca = get_index_from_uuid(ca, adresse_selectionnee["__uuid"])
                if idx_ca is not None:
                    ancienne_valeur = ca.loc[idx_ca]
                    if diff_cols_between_rows(ancienne_valeur, adresse_selectionnee):
                        undo.save()
                        st.session_state.ca.loc[idx_ca, st.session_state.ca.columns] = adresse_selectionnee[st.session_state.ca.columns]
                        sql.sauvegarder_ca()
                        st.rerun()

# Affichage du menu carnet d'adresses
def afficher_menu_ca():
    def get_nouveau_nom(ca):
        noms_existants = ca["Nom"].dropna().astype(str).str.strip().tolist()
        compteur = 0
        while True:
            compteur += 1
            nom_candidat = f"Nom {compteur}"
            if nom_candidat not in noms_existants:
                return nom_candidat

    ca = st.session_state.get("ca")
    if ca is not None and est_contexte_valide():
        with st.sidebar.expander("Carnet d'adresses", expanded=True):
            
            adresse_selectionnee = st.session_state.ca_adresse_selectionnee
            lieu_selectionne = adresse_selectionnee["Nom"] if isinstance(adresse_selectionnee, pd.Series) else "..."

            st_info_avec_label(None, lieu_selectionne, afficher_label=False)

            if st.button(LABEL_BOUTON_SUPPRIMER, use_container_width=True, disabled=not isinstance(adresse_selectionnee, pd.Series), key="supprimer_addr"):

                undo.save()
                st.session_state.ca = ca[ca["__uuid"] != adresse_selectionnee["__uuid"]]
                sql.sauvegarder_ca()

                ca_display = st.session_state.get("ca_display")
                if isinstance(ca_display, pd.DataFrame):
                    st.session_state.ca_display = ca_display[ca_display["__uuid"] != adresse_selectionnee["__uuid"]]
                    demander_selection("carnet_adresses", ligne_voisine_uuid(ca_display, adresse_selectionnee["__uuid"]))
                    st.rerun()

            if st.button(LABEL_BOUTON_NOUVELLE_ADRESSE, use_container_width=True, key="ajouter_addr"):

                undo.save()
                new_uuid = str(uuid.uuid4())
                new_name = get_nouveau_nom(ca)
                new_row = {"Nom": new_name, "Adresse": None, "__uuid": new_uuid}
                st.session_state.ca = pd.concat([ca, pd.DataFrame([new_row])], ignore_index=True)
                sql.sauvegarder_ca()

                ca_display = st.session_state.get("ca_display")
                if isinstance(ca_display, pd.DataFrame):
                    new_row = {col: None for col in ca_display.columns}
                    new_row["Nom"] = new_name
                    new_row["__uuid"] = new_uuid
                    st.session_state.ca_display = pd.concat([ca_display, pd.DataFrame([new_row])], ignore_index=True)
                    new_idx = get_index_from_uuid(st.session_state.ca_display, new_uuid)
                    demander_selection("carnet_adresses", new_idx)
                    st.rerun()

# Essai infructueux pour éviter le blocage de l'UI au retour d'appel d'une page web dans le meme onglet (same tab) sur IOS
@st.cache_resource
def inject_ios_soft_revive():
    st.markdown("""
        <script>
        (function(){
        const ua = navigator.userAgent || "";
        const isIOS =
            /iPad|iPhone|iPod/.test(ua) ||
            (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) ||
            (ua.includes("Mac") && "ontouchend" in window);

        function cameFromBackForward(){
            try {
            var nav = performance.getEntriesByType && performance.getEntriesByType('navigation');
            return !!(nav && nav[0] && nav[0].type === 'back_forward');
            } catch(e){ return false; }
        }

        function softRevive(){
            try { document.activeElement && document.activeElement.blur && document.activeElement.blur(); } catch(e){}
            try { window.dispatchEvent(new Event('focus')); } catch(e){}
            try { window.dispatchEvent(new Event('resize')); } catch(e){}
            // petit “reflow” pour réveiller WebKit
            try {
            var html = document.documentElement;
            var prev = html.style.webkitTransform;
            html.style.webkitTransform = 'translateZ(0)';
            void html.offsetHeight;
            html.style.webkitTransform = prev || '';
            } catch(e){}
        }

        window.addEventListener('pageshow', function(e){
            if (!isIOS) return;
            if (e.persisted || cameFromBackForward()){
            // réveille la page parent
            softRevive();
            // Laisse les iframes (grilles) gérer leur propre refresh (voir 2B)
            }
        }, false);
        })();
        </script>
    """, unsafe_allow_html=True)
    return True

# Essai infructueux pour éviter le blocage de l'UI au retour d'appel d'une page web dans le meme onglet (same tab) sur IOS
@st.cache_resource
def inject_ios_hard_revive():
    st.markdown("""
    <script>
    (function () {
      if (window.__iosHardReviveInstalled) return; window.__iosHardReviveInstalled = true;
      const ua = navigator.userAgent || "";
      const isIOS =
        /iPad|iPhone|iPod/.test(ua) ||
        (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) ||
        (ua.includes("Mac") && "ontouchend" in window);

      function cameFromBackForward(){
        try {
          var nav = performance.getEntriesByType && performance.getEntriesByType('navigation');
          return !!(nav && nav[0] && nav[0].type === 'back_forward');
        } catch(e){ return false; }
      }

      function markLeaving(){
        try { sessionStorage.setItem("__ios_expect_return","1"); } catch(_) {}
      }
      window.__iosRevive = { markLeaving: markLeaving };

      // 👂 Reçoit le signal depuis l'iframe (renderer) pour marquer le départ
      window.addEventListener("message", function(ev){
        try {
          var d = ev && ev.data;
          if (d && d.__ios_mark_leaving === 1) { markLeaving(); }
        } catch(_){}
      }, false);

      function shouldReload(e){
        var expect="0", last=0, now=Date.now();
        try { expect = sessionStorage.getItem("__ios_expect_return") || "0"; } catch(_){}
        try { last = parseInt(sessionStorage.getItem("__ios_hard_reload_ts")||"0",10); } catch(_){}
        var fromBF = (e && e.persisted) || cameFromBackForward();
        return { expect: expect==="1", fromBF, last, now };
      }

      function hardReloadGuarded(){
        var now = Date.now(), last = 0;
        try { last = parseInt(sessionStorage.getItem("__ios_hard_reload_ts")||"0",10); } catch(_){}
        if (now - last < 3000) return; // anti-boucle 3s
        try { sessionStorage.setItem("__ios_hard_reload_ts", String(now)); } catch(_){}
        try { sessionStorage.removeItem("__ios_expect_return"); } catch(_){}
        try { location.reload(); } catch(_) { location.assign(location.href); }
      }

      // pageshow (retour vers l’onglet)
      window.addEventListener("pageshow", function(e){
        var st = shouldReload(e);
        if (st.expect || st.fromBF) { hardReloadGuarded(); }
        else { try { sessionStorage.removeItem("__ios_expect_return"); } catch(_){ } }
      }, false);

      // Bonus: si Safari ne déclenche pas pageshow, on tente via visibilitychange
      document.addEventListener("visibilitychange", function(){
        if (document.visibilityState === "visible") {
          var st = shouldReload(null);
          if (st.expect) { hardReloadGuarded(); }
        }
      }, false);
    })();
    </script>
    """, unsafe_allow_html=True)
    return True

# Essai infructueux pour éviter le blocage de l'UI au retour d'appel d'une page web dans le meme onglet (same tab) sur IOS
@st.cache_resource
def inject_ios_always_reload_on_return():
    st.markdown("""
    <script>
    (function () {
      if (window.__iosAlwaysReloadInstalled) return; window.__iosAlwaysReloadInstalled = true;

      const ua = navigator.userAgent || "";
      const isIOS =
        /iPad|iPhone|iPod/.test(ua) ||
        (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) ||
        (ua.includes("Mac") && "ontouchend" in window);
      if (!isIOS) return;

      function guardReload() {
        var now = Date.now();
        var last = 0;
        try { last = parseInt(sessionStorage.getItem("__ios_last_reload_ts")||"0", 10); } catch(_){}
        if (now - last < 3000) return; // anti-boucle 3s
        try { sessionStorage.setItem("__ios_last_reload_ts", String(now)); } catch(_){}
        // plusieurs tentatives pour forcer le rechargement
        try { location.reload(); return; } catch(_){}
        try { location.replace(location.href); return; } catch(_){}
        try { window.location.assign(window.location.href); return; } catch(_){}
      }

      // 1) Recharger à every 'pageshow' (retour dans l'onglet)
      window.addEventListener("pageshow", function(){ guardReload(); }, false);

      // 2) Fallback si pageshow ne part pas : quand la page redevient visible
      document.addEventListener("visibilitychange", function(){
        if (document.visibilityState === "visible") { guardReload(); }
      }, false);

      // 3) Fallback supplémentaire : regain de focus
      window.addEventListener("focus", function(){ guardReload(); }, false);
    })();
    </script>
    """, unsafe_allow_html=True)
    return True

# Essai infructueux pour éviter le blocage de l'UI au retour d'appel d'une page web dans le meme onglet (same tab) sur IOS
@st.cache_resource
def inject_ios_watchdog_reload():
    st.markdown("""
    <script>
    (function () {
      if (window.__iosWatchdogInstalled) return; window.__iosWatchdogInstalled = true;
      const ua = navigator.userAgent || "";
      const isIOS =
        /iPad|iPhone|iPod/.test(ua) ||
        (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) ||
        (ua.includes("Mac") && "ontouchend" in window);
      if (!isIOS) return;

      var lastBeat = Date.now();
      var beatRAF  = null, guardTsKey="__ios_last_reload_ts";

      function rafLoop(){
        lastBeat = Date.now();
        try { beatRAF = requestAnimationFrame(rafLoop); } catch(_) {}
      }
      try { beatRAF = requestAnimationFrame(rafLoop); } catch(_) {}

      function hardReloadGuarded(){
        var now = Date.now(), last = 0;
        try { last = parseInt(sessionStorage.getItem(guardTsKey)||"0",10); } catch(_){}
        if (now - last < 3000) return; // anti-boucle 3s
        try { sessionStorage.setItem(guardTsKey, String(now)); } catch(_){}
        try { location.reload(); return; } catch(_){}
        try { location.replace(location.href); return; } catch(_){}
        try { window.location.assign(window.location.href); return; } catch(_){}
      }

      // Watchdog : si la page est visible mais que RAF ne bat pas → reload
      var watchdog = setInterval(function(){
        if (document.visibilityState !== "visible") return;
        var idle = Date.now() - lastBeat;
        if (idle > 1000) { // RAF n'a pas battu depuis >1s : UI probablement figée
          hardReloadGuarded();
        }
      }, 700);

      // Un petit coup de pouce quand on redevient visible : on redémarre RAF proprement
      document.addEventListener("visibilitychange", function(){
        if (document.visibilityState === "visible"){
          lastBeat = Date.now();
          try { cancelAnimationFrame(beatRAF); } catch(_){}
          try { beatRAF = requestAnimationFrame(rafLoop); } catch(_){}
        }
      }, false);

      // Et si on reprend le focus (ex. retour depuis Maps)
      window.addEventListener("focus", function(){
        lastBeat = Date.now();
        try { cancelAnimationFrame(beatRAF); } catch(_){}
        try { beatRAF = requestAnimationFrame(rafLoop); } catch(_){}
      }, false);
    })();
    </script>
    """, unsafe_allow_html=True)
    return True

# Essai infructueux pour éviter le blocage de l'UI au retour d'appel d'une page web dans le meme onglet (same tab) sur IOS
@st.cache_resource
def inject_ios_disable_bfcache():
    st.markdown("""
    <script>
    (function () {
      if (window.__iosNoBFCacheInstalled) return; window.__iosNoBFCacheInstalled = true;
      const ua = navigator.userAgent || "";
      const isIOS =
        /iPad|iPhone|iPod/.test(ua) ||
        (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) ||
        (ua.includes("Mac") && "ontouchend" in window);
      if (!isIOS) return;

      // 1) Désactive le Back/Forward Cache sur iOS :
      //    La présence d'un listener 'unload' suffit à empêcher bfcache.
      window.addEventListener('unload', function(){ /* nop */ }, {passive:true});

      // 2) Au retour dans l’onglet, force un vrai reload réseau (anti-boucle 3s)
      function hardReloadGuarded(){
        var now = Date.now();
        var last = 0;
        try { last = parseInt(sessionStorage.getItem("__ios_last_reload_ts")||"0",10); } catch(_){}
        if (now - last < 3000) return; // garde anti-boucle
        try { sessionStorage.setItem("__ios_last_reload_ts", String(now)); } catch(_){}
        // cache-buster pour forcer un chargement frais
        var url = location.href;
        var sep = url.indexOf('?') === -1 ? '?' : '&';
        try { location.replace(url + sep + "_ts=" + now); }
        catch(_) { location.assign(url + sep + "_ts=" + now); }
      }

      window.addEventListener('pageshow', function(){
        // Comme le bfcache est désactivé, on revient déjà via un "vrai" load,
        // mais si Safari recycle malgré tout, on a un filet de sécurité :
        hardReloadGuarded();
      }, false);
    })();
    </script>
    """, unsafe_allow_html=True)
    return True


# Initialisation de la page HTML
def initialiser_page():

    # Coller ici les essais pour éviter le blocage de l'UI au retour d'appel d'une page web dans le meme onglet (same tab) sur IOS
    pass

