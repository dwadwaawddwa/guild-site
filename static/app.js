// ================= Referentiel items Albion =================
let ITEMS = null;
let NAME_TO_UNIQUE = {};   // nom FR (valeur stockee) -> uniquename
let NAME_TO_EN = {};       // nom FR -> nom EN
let NAME_TO_TERMS = {};    // nom FR -> mots-cles de categorie (FR+EN)
const LANG = document.documentElement.dataset.lang || "fr";

// minuscule + sans accents : "Épée" et "epee" deviennent identiques
function norm(s) {
  return String(s == null ? "" : s).toLowerCase()
    .normalize("NFD").replace(/[̀-ͯ]/g, "");
}

async function loadItems() {
  if (ITEMS) return ITEMS;
  try { ITEMS = await (await fetch("/static/albion_items.json")).json(); }
  catch (e) { ITEMS = {}; }
  for (const cat of Object.keys(ITEMS))
    for (const it of ITEMS[cat]) {
      NAME_TO_UNIQUE[it.name] = it.unique;
      if (it.name_en) NAME_TO_EN[it.name] = it.name_en;
      if (it.terms) NAME_TO_TERMS[it.name] = it.terms;
    }
  return ITEMS;
}

// nom a afficher selon la langue choisie (la valeur stockee reste le nom FR)
function itemLabel(frName) {
  if (!frName) return "";
  return (LANG === "en" && NAME_TO_EN[frName]) ? NAME_TO_EN[frName] : frName;
}
// texte de recherche d'un item : nom FR + nom EN + categorie (epee/sword...),
// sans accents -> on peut chercher dans n'importe quelle langue
function itemHaystack(it) {
  return norm((it.name || "") + " " + (it.name_en || "") + " " + (it.terms || ""));
}
function iconUrl(u) {
  return "https://render.albiononline.com/v1/item/" + encodeURIComponent(u) + ".png?quality=1";
}
async function hydrateItemIcons() {
  await loadItems();
  // tout img avec data-item (icones normales ET badges de swap)
  document.querySelectorAll("img[data-item]").forEach(img => {
    const val = img.getAttribute("data-item");
    if (!val) { img.style.visibility = "hidden"; return; }
    img.loading = "lazy";
    img.src = iconUrl(NAME_TO_UNIQUE[val] || val);
    img.onerror = () => { img.style.visibility = "hidden"; };
  });
}
function csrf() { return document.querySelector('meta[name="csrf"]').content; }

// echappe toute valeur venant d'un utilisateur avant injection HTML (anti-XSS)
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ================= Selecteur d'items visuel (modal partage) =================
let _activeSlot = null;

function initSlotIcon(slot) {
  const input = slot.querySelector("input[type=hidden]");
  const img = slot.querySelector(".item-ico");
  const val = slot.querySelector(".slot-value");
  if (input && input.value) {
    img.src = iconUrl(NAME_TO_UNIQUE[input.value] || input.value);
    img.style.visibility = "visible";
    img.onerror = () => { img.style.visibility = "hidden"; };
    if (val) val.textContent = itemLabel(input.value);
  } else {
    if (img) img.style.visibility = "hidden";
    if (val) val.textContent = "—";
  }
}

async function setupItemPickers() {
  await loadItems();
  const modal = document.getElementById("itemModal");
  if (!modal) return;
  const grid = document.getElementById("itemGrid");
  const search = document.getElementById("itemSearch");

  function render(cat, q) {
    q = norm(q);
    // recherche FR ou EN, par nom ou par categorie ("sword", "epee"...)
    const list = (ITEMS[cat] || []).filter(it => itemHaystack(it).includes(q)).slice(0, 200);
    grid.innerHTML = "";
    const empty = document.createElement("div");
    empty.className = "item-tile empty";
    empty.innerHTML = "<span>✕</span><small>aucun</small>";
    empty.onclick = () => pick(null);
    grid.appendChild(empty);
    for (const it of list) {
      const label = itemLabel(it.name);
      const t = document.createElement("div");
      t.className = "item-tile"; t.title = label;
      t.innerHTML = `<img loading="lazy" src="${esc(iconUrl(it.unique))}"><small>${esc(label)}</small>`;
      t.onclick = () => pick(it);
      grid.appendChild(t);
    }
    if (!list.length) grid.insertAdjacentHTML("beforeend", '<p class="muted">Rien trouvé — précise ta recherche.</p>');
  }
  function pick(it) {
    if (!_activeSlot) return;
    const img = _activeSlot.querySelector(".item-ico");
    const val = _activeSlot.querySelector(".slot-value");
    const input = _activeSlot.querySelector("input[type=hidden]");
    if (it) {
      img.src = iconUrl(it.unique); img.style.visibility = "visible";
      if (val) val.textContent = itemLabel(it.name);
      input.value = it.name;  // on stocke toujours le nom FR (canonique)
    } else {
      img.removeAttribute("src"); img.style.visibility = "hidden";
      if (val) val.textContent = "—"; input.value = "";
    }
    close();
  }
  function openFor(slot) {
    _activeSlot = slot; search.value = "";
    render(slot.dataset.cat, ""); modal.classList.remove("hidden"); search.focus();
  }
  function close() { modal.classList.add("hidden"); _activeSlot = null; }

  // delegation : marche aussi pour les slots ajoutes dynamiquement
  document.addEventListener("click", (e) => {
    const inModal = e.target.closest("#itemModal");
    if (inModal) return;
    const slot = e.target.closest(".slot[data-cat]");
    if (slot) openFor(slot);
  });
  search.oninput = () => { if (_activeSlot) render(_activeSlot.dataset.cat, search.value); };
  document.getElementById("itemClose").onclick = close;
  modal.onclick = (e) => { if (e.target === modal) close(); };

  document.querySelectorAll(".slot[data-cat]").forEach(initSlotIcon);
}

function itemSlotHTML(cat, label) {
  return `<div class="slot" data-cat="${cat}">
    <div class="slot-ico"><img class="item-ico big"></div>
    <div class="slot-label">${label}</div>
    <div class="slot-value muted small">—</div>
    <input type="hidden" value="">
  </div>`;
}

// ================= Selection multiple ordonnee de builds (call) =================
function initBuildPicker() {
  const form = document.getElementById("signupForm");
  if (!form) return;
  const chosen = document.getElementById("chosenBuilds");
  const cards = [...form.querySelectorAll(".pick-build")];
  let order = [];
  try { order = JSON.parse(document.getElementById("myChoices").textContent || "[]").map(String); } catch (e) {}
  function refresh() {
    cards.forEach(card => {
      const id = card.dataset.buildId, pos = order.indexOf(id), badge = card.querySelector(".pick-order");
      if (pos >= 0) { card.classList.add("selected"); badge.textContent = pos + 1; }
      else { card.classList.remove("selected"); badge.textContent = ""; }
    });
    chosen.value = order.join(",");
  }
  cards.forEach(card => card.addEventListener("click", () => {
    const id = card.dataset.buildId, pos = order.indexOf(id);
    if (pos >= 0) order.splice(pos, 1); else order.push(id);
    refresh();
  }));
  form.addEventListener("submit", (e) => {
    if (!order.length) { e.preventDefault(); alert("Clique au moins un build (dans l'ordre de préférence)."); }
  });
  refresh();
}

// ================= Recherche de page (builds, compos) =================
async function initPageSearch(inputId, cardSel) {
  const input = document.getElementById(inputId);
  if (!input) return;
  await loadItems();
  const cards = [...document.querySelectorAll(cardSel)];
  cards.forEach(card => {
    let extra = "";
    card.querySelectorAll("img[data-item]").forEach(img => {
      const v = img.getAttribute("data-item");
      if (v) extra += " " + v + " " + (NAME_TO_EN[v] || "") + " " + (NAME_TO_TERMS[v] || "");
    });
    card._hay = norm((card.dataset.search || "") + " " + extra);
  });
  function apply() {
    const q = norm(input.value.trim());
    cards.forEach(c => { c.style.display = (!q || c._hay.includes(q)) ? "" : "none"; });
    // masque les titres de categorie devenus vides (page Builds groupee)
    document.querySelectorAll(".build-grid").forEach(g => {
      const vis = [...g.children].some(ch => ch.style.display !== "none");
      g.style.display = vis ? "" : "none";
      const h = g.previousElementSibling;
      if (h && h.classList.contains("cat-head")) h.style.display = vis ? "" : "none";
    });
  }
  input.addEventListener("input", apply);
}

// ================= Recherche dans le Destiny Board =================
function initDestinySearch() {
  const input = document.getElementById("dbSearch");
  if (!input) return;
  const rows = [...document.querySelectorAll(".db-row")];
  const cols = [...document.querySelectorAll(".db-col")];
  // pre-calcule le texte de recherche (nom FR + EN + categorie), sans accents
  rows.forEach(r => { r._hay = norm(r.dataset.name || ""); });
  input.addEventListener("input", () => {
    const q = norm(input.value.trim());
    rows.forEach(r => {
      r.style.display = (!q || r._hay.includes(q)) ? "" : "none";
    });
    // masque les colonnes vides et deplie les sections
    cols.forEach(c => {
      const visible = [...c.querySelectorAll(".db-row")].some(r => r.style.display !== "none");
      c.style.display = visible ? "" : "none";
    });
    if (q) document.querySelectorAll("details.arch").forEach(d => d.open = true);
  });
}

// ================= Createur de roster (callers) =================
function initRoster() {
  const dataEl = document.getElementById("rosterData");
  if (!dataEl) return;
  const roster = JSON.parse(dataEl.textContent || "[]");
  const out = document.getElementById("rosterOut");
  const filterSel = document.getElementById("rosterFilter");
  const minWrap = document.getElementById("minWrap");
  const minInput = document.getElementById("minSpec");
  const payload = document.getElementById("rosterPayload");
  const countEl = document.getElementById("rosterCount");
  const chosenEl = document.getElementById("rosterChosen");

  // selection finale : ref -> user_id
  const selection = {};
  let touched = false;  // le caller a-t-il deja choisi manuellement ?
  if (chosenEl) {
    try {
      for (const r of JSON.parse(chosenEl.textContent || "[]")) {
        if (r.user_id) { selection[r.ref] = String(r.user_id); touched = true; }
      }
    } catch (e) {}
  }

  function rank(cands, mode, minSpec) {
    let list = cands.slice();
    if (mode === "fcfs_min") list = list.filter(c => (c.spec || 0) >= minSpec);
    if (mode === "spec") {
      list.sort((a, b) => (b.spec || 0) - (a.spec || 0) || a.prio - b.prio || (a.at > b.at ? 1 : -1));
    } else {
      list.sort((a, b) => a.prio - b.prio || (a.at > b.at ? 1 : a.at < b.at ? -1 : 0));
    }
    return list;
  }

  function syncPayload() {
    const clean = {};
    for (const k in selection) if (selection[k]) clean[k] = selection[k];
    if (payload) payload.value = JSON.stringify(clean);
    if (countEl) {
      const n = Object.keys(clean).length;
      countEl.textContent = n + " place" + (n > 1 ? "s" : "") + " affectée" + (n > 1 ? "s" : "");
    }
  }

  function render() {
    const mode = filterSel.value;
    const minSpec = parseInt(minInput.value || "0", 10);
    minWrap.style.display = mode === "fcfs_min" ? "" : "none";
    out.innerHTML = "";
    for (const slot of roster) {
      const list = rank(slot.candidates, mode, minSpec);
      // pre-selection auto du 1er tant que le caller n'a rien touche manuellement
      if (!touched && !(slot.ref in selection) && list.length) selection[slot.ref] = String(list[0].user_id);
      const card = document.createElement("div");
      card.className = "roster-card" + (list.length ? "" : " empty");
      const rows = list.map(c => {
        const sel = selection[slot.ref] === String(c.user_id);
        return `<li class="r-cand${sel ? " chosen" : ""}" data-ref="${esc(slot.ref)}" data-uid="${esc(String(c.user_id))}">
          <span class="prio-badge">P${Number(c.prio) || 0}</span>
          <span class="r-user">${esc(c.user)}</span>
          <span class="r-spec" title="Spec totale sur la ligne d'arme">${Number(c.spec) || 0}</span>
          <span class="status status-${esc(c.status)}">${esc(c.status)}</span>
          <span class="r-check">${sel ? "✅" : ""}</span>
        </li>`;
      }).join("");
      card.innerHTML = `<h3>${esc(slot.label)} <span class="muted small">(${list.length})</span></h3>` +
        (list.length ? `<ul class="roster-list">${rows}</ul>`
                     : '<p class="muted small">Personne ne correspond.</p>');
      out.appendChild(card);
    }
    out.querySelectorAll(".r-cand").forEach(li => {
      li.onclick = () => {
        touched = true;
        const ref = li.dataset.ref, uid = li.dataset.uid;
        selection[ref] = (selection[ref] === uid) ? "" : uid;  // reclic = desaffecte
        render();
      };
    });
    syncPayload();
  }
  filterSel.onchange = render;
  minInput.oninput = render;
  render();
}

// ================= Editeur de compositions (selection de build) =================
async function initCompEditor() {
  await loadItems();
  const CATS = JSON.parse(document.getElementById("catData").textContent);  // [[key,label],...]
  const catLabel = Object.fromEntries(CATS);
  const compData = JSON.parse(document.getElementById("compData").textContent || "null");
  const BUILDS = JSON.parse(document.getElementById("buildsData").textContent || "[]");
  const buildById = Object.fromEntries(BUILDS.map(b => [String(b.id), b]));
  const container = document.getElementById("slotsContainer");
  const withMembers = container.dataset.members !== "0";  // invite : pas de membres a assigner
  let members = [];
  if (withMembers) {
    try { members = await (await fetch("/api/members")).json(); } catch (e) {}
  }
  const GEAR_KEYS = ["weapon","offhand","head","chest","shoes","cape","food","potion"];

  function catOptions(selected) {
    let h = "";
    for (const [k, l] of CATS) h += `<option value="${k}" ${k===selected?"selected":""}>${l}</option>`;
    h += `<option value="__other" ${selected==="__other"?"selected":""}>Autre rôle…</option>`;
    return h;
  }
  function memberOptions(sel) {
    let h = '<option value="">— non assigné —</option>';
    for (const m of members) h += `<option value="${esc(m.id)}" ${m.id==sel?"selected":""}>${esc(m.name)}</option>`;
    return h;
  }
  function ico(v) { return v ? iconUrl(NAME_TO_UNIQUE[v] || v) : ""; }
  function buildPreviewHTML(b) {
    if (!b) return '<span class="muted">Choisir un build…</span>';
    const w = ico(b.weapon);
    return `${w?`<img class="bp-ico" src="${esc(w)}">`:""}<span class="bp-name">${esc(b.name)}</span>`;
  }

  // --- modal de selection de build (images + noms) ---
  const modal = document.getElementById("buildModal");
  const grid = document.getElementById("buildGrid");
  const search = document.getElementById("buildSearch");
  const catFilter = document.getElementById("buildCatFilter");
  let activeBtn = null;
  function renderBuilds(q) {
    q = norm(q);
    const cat = catFilter ? catFilter.value : "";
    grid.innerHTML = "";
    const empty = document.createElement("div");
    empty.className = "build-card empty"; empty.innerHTML = "<h3>✕ aucun</h3>";
    empty.onclick = () => pickBuild(null); grid.appendChild(empty);
    const matches = BUILDS.filter(b => {
      if (cat && b.category !== cat) return false;
      if (!q) return true;
      // recherche par nom de build, par piece d'equipement ou par categorie
      // d'arme ("sword"/"epee"), en FR comme en EN
      const hay = norm([(b.name || "")].concat(
        GEAR_KEYS.flatMap(k => {
          const v = b[k] || "";
          return v ? [v, NAME_TO_EN[v] || "", NAME_TO_TERMS[v] || ""] : [];
        })
      ).join(" "));
      return hay.includes(q);
    });
    for (const b of matches) {
      const sw = b.swaps || {};
      const items = GEAR_KEYS.filter(k => b[k]).map(k => {
        const v = b[k], s = sw[k];
        const corner = s ? `<img class="swap-corner sm" src="${esc(ico(s))}" title="Swap : ${esc(itemLabel(s))}">` : "";
        return `<span class="ico-wrap"><img class="item-ico" src="${esc(ico(v))}" title="${esc(itemLabel(v))}">${corner}</span>`;
      }).join("");
      const clabel = b.category ? (catLabel[b.category] || b.category) : "";
      const card = document.createElement("div"); card.className = "build-card";
      card.innerHTML = `<h3>${esc(b.name)}</h3>` +
        (clabel ? `<span class="tag tag-cat">${esc(clabel)}</span>` : "") +
        `<div class="items-row">${items}</div>`;
      card.onclick = () => pickBuild(b);
      grid.appendChild(card);
    }
    if (!matches.length) grid.insertAdjacentHTML("beforeend",
      `<p class="muted">${BUILDS.length ? "Aucun build ne correspond." : "Aucun build. Crée-en dans la page Builds."}</p>`);
  }
  function pickBuild(b) {
    if (!activeBtn) return;
    activeBtn.dataset.buildId = b ? b.id : "";
    activeBtn.innerHTML = buildPreviewHTML(b);
    close();
  }
  function close() { modal.classList.add("hidden"); activeBtn = null; }
  function openBuilds(btn) { activeBtn = btn; search.value = ""; renderBuilds(""); modal.classList.remove("hidden"); search.focus(); }
  search.oninput = () => renderBuilds(search.value);
  if (catFilter) catFilter.onchange = () => renderBuilds(search.value);
  document.getElementById("buildClose").onclick = close;
  modal.onclick = (e) => { if (e.target === modal) close(); };

  function addSlot(d) {
    d = d || {role_name:"", category:"", build_id:null, assigned_user_id:""};
    const known = CATS.some(([k]) => k === d.category);
    const catVal = known ? d.category : (d.role_name ? "__other" : CATS[0][0]);
    const b = d.build_id ? buildById[String(d.build_id)] : null;
    const card = document.createElement("div");
    card.className = "edit-slot";
    card.innerHTML = `
      <div class="es-top">
        <label class="es-field">Rôle<select class="es-cat">${catOptions(catVal)}</select></label>
        <label class="es-field es-role-wrap" style="${catVal==="__other"?"":"display:none"}">Nom du rôle<input class="es-role" value="${esc(d.role_name)}" placeholder="ex: Battlemount"></label>
        <label class="es-field es-build-field">Build
          <button type="button" class="build-pick-btn" data-build-id="${b?b.id:""}">${buildPreviewHTML(b)}</button>
        </label>
        <label class="es-field" ${withMembers ? "" : "hidden"}>Membre<select class="es-member">${memberOptions(d.assigned_user_id)}</select></label>
        <button class="btn btn-sm btn-danger es-del" type="button">✕</button>
      </div>`;
    const catSel = card.querySelector(".es-cat");
    const roleWrap = card.querySelector(".es-role-wrap");
    catSel.addEventListener("change", () => { roleWrap.style.display = catSel.value === "__other" ? "" : "none"; });
    card.querySelector(".build-pick-btn").onclick = (e) => openBuilds(e.currentTarget);
    card.querySelector(".es-del").onclick = () => card.remove();
    container.appendChild(card);
  }

  document.getElementById("addSlot").onclick = () => addSlot();
  if (compData && compData.slots && compData.slots.length) compData.slots.forEach(addSlot);
  else addSlot();

  document.getElementById("saveComp").onclick = async () => {
    const slots = [...container.querySelectorAll(".edit-slot")].map(card => {
      const catVal = card.querySelector(".es-cat").value;
      const isOther = catVal === "__other";
      const role = isOther ? card.querySelector(".es-role").value : (catLabel[catVal] || "");
      const msel = card.querySelector(".es-member");
      const bid = card.querySelector(".build-pick-btn").dataset.buildId || "";
      return { role_name: role, category: isOther ? "" : catVal, build_id: bid || null,
               assigned_user_id: msel.value,
               assigned_username: msel.value ? msel.options[msel.selectedIndex].text : "" };
    });
    const pub = document.getElementById("compPublic");
    const payload = {
      id: compData ? compData.id : null, csrf: csrf(),
      name: document.getElementById("compName").value,
      content_type: document.getElementById("compType").value,
      notes: document.getElementById("compNotes").value,
      public: pub ? pub.checked : false, slots,
    };
    const status = document.getElementById("saveStatus");
    status.textContent = "Enregistrement...";
    const r = await fetch("/compositions/save", {
      method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(payload),
    });
    if (r.ok) {
      const dd = await r.json();
      status.textContent = "✅ Enregistré.";
      if (!compData) window.location.href = "/compositions/" + dd.id;
    } else { status.textContent = "❌ Erreur lors de l'enregistrement."; }
  };
}
