/* Listas, buscador y calendario con el estilo de Decopet.
   Los campos del formulario son los de siempre: se ven igual, se envían igual y el resto del código los usa igual.
   Lo único que cambia es lo que se abre al tocarlos: en vez de la lista o el calendario de Windows, uno del ERP.
   En el teléfono se dejan los del teléfono: la rueda para elegir es más cómoda con el dedo que cualquier lista.
   Dónde se usa: ZONA (por ahora, el panel de Nueva orden). Para sumar otra pantalla, se agrega su selector. */
(function () {
  const ZONA = 'form.no-form';
  if (!matchMedia('(pointer:fine)').matches) return;

  const MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
  const DIAS = ['do', 'lu', 'ma', 'mi', 'ju', 'vi', 'sá'];
  let abierto = null;   // solo un desplegable abierto a la vez

  function cerrar() {
    if (!abierto) return;
    abierto._dueno.setAttribute('aria-expanded', 'false');
    abierto.remove(); abierto = null;
  }
  function esDe(dueno) { return abierto && abierto._dueno === dueno; }
  document.addEventListener('mousedown', e => { if (abierto && !abierto.contains(e.target) && e.target !== abierto._dueno) cerrar(); }, true);
  document.addEventListener('scroll', e => { if (abierto && !abierto.contains(e.target)) cerrar(); }, true);
  addEventListener('resize', cerrar);

  // la caja flotante, pegada al campo: abajo si cabe, arriba si no
  function caja(dueno, clase) {
    cerrar();
    const p = document.createElement('div');
    p.className = 'ctl-pop ' + (clase || '');
    p._dueno = dueno; dueno.setAttribute('aria-expanded', 'true');
    document.body.appendChild(p); abierto = p;
    return p;
  }
  function colocar(p) {
    const r = p._dueno.getBoundingClientRect();
    p.style.minWidth = r.width + 'px'; p.style.maxHeight = '';
    const abajo = innerHeight - r.bottom - 12, arriba = r.top - 12, h = p.offsetHeight;
    // el calendario no debe quedar cortado: si no cabe ni abajo ni arriba, va al lado del campo, centrado en la pantalla
    if (p.classList.contains('ctl-cal') && h > abajo && h > arriba) {
      p.style.minWidth = '';
      const izq = r.left - p.offsetWidth - 8;
      p.style.left = (izq >= 8 ? izq : Math.min(r.right + 8, innerWidth - p.offsetWidth - 8)) + 'px';
      p.style.top = Math.max(8, Math.min(r.top + r.height / 2 - h / 2, innerHeight - h - 8)) + 'px';
      return;
    }
    if (h > abajo && arriba > abajo) { p.style.maxHeight = arriba + 'px'; p.style.top = Math.max(8, r.top - 6 - Math.min(h, arriba)) + 'px'; }
    else { p.style.maxHeight = abajo + 'px'; p.style.top = (r.bottom + 6) + 'px'; }
    p.style.left = Math.max(8, Math.min(r.left, innerWidth - p.offsetWidth - 8)) + 'px';
  }

  // ── lista de opciones (para los select y para el buscador)
  function lista(dueno, items, alElegir, actual, vacio) {
    const p = caja(dueno, 'ctl-lista'); p.setAttribute('role', 'listbox');
    items.forEach(it => {
      const o = document.createElement('div');
      o.className = 'ctl-op' + (it.valor === actual ? ' sel' : '') + (it.des ? ' des' : '');
      o.setAttribute('role', 'option');
      const t = document.createElement('span'); t.textContent = it.texto; o.appendChild(t);
      if (it.sub) { const s = document.createElement('small'); s.textContent = it.sub; o.appendChild(s); }
      if (!it.des) o.addEventListener('mousedown', e => { e.preventDefault(); cerrar(); alElegir(it); });
      o.addEventListener('mousemove', () => enfocar(o));
      p.appendChild(o);
    });
    if (!items.length) { const v = document.createElement('div'); v.className = 'ctl-vacio'; v.textContent = vacio || 'Sin resultados'; p.appendChild(v); }
    colocar(p);
    enfocar(p.querySelector('.ctl-op.sel:not(.des)') || p.querySelector('.ctl-op:not(.des)'));
  }
  function enfocar(o) {
    if (!o || !abierto) return;
    abierto.querySelectorAll('.foco').forEach(x => x.classList.remove('foco'));
    o.classList.add('foco'); o.scrollIntoView({ block: 'nearest' });
  }
  function mover(d) {
    const ops = [...abierto.querySelectorAll('.ctl-op:not(.des)')];
    if (!ops.length) return;
    const i = ops.indexOf(abierto.querySelector('.ctl-op.foco'));
    enfocar(ops[Math.max(0, Math.min(ops.length - 1, i + d))]);
  }
  function elegirFoco() {
    const o = abierto && abierto.querySelector('.ctl-op.foco');
    if (!o) return false;
    o.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    return true;
  }
  function avisar(campo) {   // los onchange/oninput del formulario se enteran como si lo hubiera elegido a mano
    campo.dispatchEvent(new Event('input', { bubbles: true }));
    campo.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // ── select: el campo queda igual; solo se cambia la lista que se abre
  function mejorarSelect(sel) {
    sel.dataset.ctl = '1';
    const abrir = () => {
      if (esDe(sel)) { cerrar(); return; }
      lista(sel, [...sel.options].filter(o => !o.hidden).map(o => ({ valor: o.value, texto: o.textContent, des: o.disabled })),
        it => { if (sel.value !== it.valor) { sel.value = it.valor; avisar(sel); } sel.focus(); }, sel.value);
    };
    sel.addEventListener('mousedown', e => { if (sel.disabled) return; e.preventDefault(); sel.focus(); abrir(); });
    sel.addEventListener('keydown', e => {
      const k = e.key;
      if (esDe(sel)) {
        if (k === 'ArrowDown' || k === 'ArrowUp') { e.preventDefault(); mover(k === 'ArrowDown' ? 1 : -1); }
        else if (k === 'Enter' || k === ' ') { e.preventDefault(); elegirFoco(); }
        else if (k === 'Escape') { e.preventDefault(); e.stopPropagation(); cerrar(); }   // solo cierra la lista, no el panel
        else if (k === 'Tab') cerrar();
      } else if (k === 'Enter' || k === ' ' || k === 'ArrowDown' || (k === 'ArrowUp' && e.altKey) || k === 'F4') { e.preventDefault(); abrir(); }
    });
    sel.addEventListener('blur', () => setTimeout(() => { if (esDe(sel) && document.hasFocus() && document.activeElement !== sel) cerrar(); }, 120));
  }

  // ── buscador (campos con lista de sugerencias): se busca por nombre sin importar tildes, o por teléfono
  const plano = s => (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  function mejorarCombo(inp) {
    const dl = document.getElementById(inp.getAttribute('list'));
    if (!dl) return;
    inp.dataset.ctl = '1'; inp.dataset.lista = dl.id;
    inp.removeAttribute('list');                    // sin esto el navegador abre su propia lista además de la nuestra
    inp.setAttribute('role', 'combobox'); inp.setAttribute('autocomplete', 'off');
    const buscar = () => {
      const q = plano(inp.value.trim()), partes = q.split(/\s+/).filter(Boolean), dig = inp.value.replace(/\D/g, '');
      const r = [];
      for (const o of document.getElementById(inp.dataset.lista).options) {
        const sub = (o.textContent || '').trim(), t = plano(o.value);
        if (!partes.length || partes.every(p => t.includes(p)) || (dig.length >= 3 && sub.replace(/\D/g, '').includes(dig))) {
          r.push({ valor: o.value, texto: o.value, sub: sub && sub !== o.value ? sub : '' });
          if (r.length >= 50) break;
        }
      }
      return r;
    };
    const abrir = () => {
      // ya se eligió (el campo se escondió o coincide exacto con una opción): no hay nada que sugerir
      if (!inp.offsetParent || [...document.getElementById(inp.dataset.lista).options].some(o => o.value === inp.value)) { if (esDe(inp)) cerrar(); return; }
      lista(inp, buscar(), it => { inp.value = it.valor; avisar(inp); }, null, 'No aparece');
    };
    inp.addEventListener('input', e => { if (e.isTrusted) setTimeout(abrir, 0); });
    inp.addEventListener('focus', () => setTimeout(abrir, 0));
    inp.addEventListener('mousedown', () => { if (!esDe(inp)) setTimeout(abrir, 0); });
    const elegido = () => [...document.getElementById(inp.dataset.lista).options].some(o => o.value === inp.value);
    inp.addEventListener('keydown', e => {
      // Enter en un buscador nunca envía el formulario: elige lo resaltado o, si no hay lista, la vuelve a abrir.
      // (Antes, con la lista cerrada, enviaba la orden a medias y salía el aviso "Elige un cliente".)
      if (e.key === 'Enter' && !esDe(inp)) { e.preventDefault(); if (!elegido()) abrir(); return; }
      if (!esDe(inp)) { if (e.key === 'ArrowDown') { e.preventDefault(); abrir(); } return; }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); mover(e.key === 'ArrowDown' ? 1 : -1); }
      else if (e.key === 'Enter') { e.preventDefault(); elegirFoco(); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cerrar(); }   // solo la lista, no el panel
      else if (e.key === 'Tab') cerrar();
    });
    // Se cierra solo si el foco pasó a otro campo. Cambiar de ventana (ir a WhatsApp a ver el nombre) no la cierra.
    inp.addEventListener('blur', () => setTimeout(() => { if (esDe(inp) && document.hasFocus() && document.activeElement !== inp) cerrar(); }, 120));
  }

  // ── calendario: el campo de fecha queda igual (se puede escribir); al tocarlo se abre el del ERP
  const iso = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  const deIso = s => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || ''); return m ? new Date(+m[1], m[2] - 1, +m[3]) : null; };
  function mejorarFecha(inp) {
    inp.dataset.ctl = '1';
    const abrir = () => {
      if (esDe(inp)) { cerrar(); return; }
      const p = caja(inp, 'ctl-cal');
      let vista = deIso(inp.value) || new Date(); vista = new Date(vista.getFullYear(), vista.getMonth(), 1);
      const fuera = s => (inp.min && s < inp.min) || (inp.max && s > inp.max);
      const elegir = s => { if (fuera(s)) return; inp.value = s; avisar(inp); cerrar(); inp.focus(); };
      const pintar = () => {
        const hoy = iso(new Date()), ayer = iso(new Date(Date.now() - 864e5));
        const ini = new Date(vista.getFullYear(), vista.getMonth(), 1 - vista.getDay());
        let h = `<div class="ctl-cal-h"><button type="button" data-m="-1" aria-label="Mes anterior">‹</button><b>${MESES[vista.getMonth()]} ${vista.getFullYear()}</b><button type="button" data-m="1" aria-label="Mes siguiente">›</button></div><div class="ctl-cal-g">`;
        DIAS.forEach(d => h += `<span class="ctl-cal-d">${d}</span>`);
        for (let i = 0; i < 42; i++) {
          const d = new Date(ini.getFullYear(), ini.getMonth(), ini.getDate() + i), s = iso(d);
          const cl = [d.getMonth() !== vista.getMonth() ? 'otro' : '', s === hoy ? 'hoy' : '', s === inp.value ? 'sel' : ''].join(' ');
          h += `<button type="button" class="${cl}" data-f="${s}" ${fuera(s) ? 'disabled' : ''}>${d.getDate()}</button>`;
        }
        h += `</div><div class="ctl-cal-p">${fuera(hoy) ? '' : `<button type="button" data-f="${hoy}">Hoy</button>`}${fuera(ayer) ? '' : `<button type="button" data-f="${ayer}">Ayer</button>`}</div>`;
        p.innerHTML = h; colocar(p);
      };
      p.addEventListener('mousedown', e => {
        e.preventDefault();
        const b = e.target.closest('button'); if (!b || b.disabled) return;
        if (b.dataset.m) { vista = new Date(vista.getFullYear(), vista.getMonth() + +b.dataset.m, 1); pintar(); }
        else if (b.dataset.f) elegir(b.dataset.f);
      });
      pintar();
    };
    inp.addEventListener('mousedown', e => { if (inp.disabled) return; e.preventDefault(); inp.focus(); abrir(); });
    inp.addEventListener('keydown', e => {
      if (e.key === 'Escape' && esDe(inp)) { e.preventDefault(); e.stopPropagation(); cerrar(); }   // solo el calendario, no el panel
      else if ((e.key === 'ArrowDown' && e.altKey) || e.key === 'F4') { e.preventDefault(); abrir(); }
    });
  }

  // ── se aplica solo a lo que está en ZONA, también a lo que aparece después (panel, otra forma de pago, mascotas…)
  let pendiente = false;
  function mejorar() {
    pendiente = false;
    document.querySelectorAll(`${ZONA} select:not([data-ctl])`).forEach(mejorarSelect);
    document.querySelectorAll(`${ZONA} input[list]:not([data-ctl])`).forEach(mejorarCombo);
    document.querySelectorAll(`${ZONA} input[type=date]:not([data-ctl])`).forEach(mejorarFecha);
  }
  new MutationObserver(() => { if (!pendiente) { pendiente = true; requestAnimationFrame(mejorar); } })
    .observe(document.documentElement, { childList: true, subtree: true });
  mejorar();
})();
