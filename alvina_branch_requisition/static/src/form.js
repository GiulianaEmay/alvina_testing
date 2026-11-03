/** @odoo-module **/
function setup() {
    const form = document.getElementById('alvina-request-form');
    if (!form || form.dataset.ready) return;
    form.dataset.ready = '1';
    const catalog = JSON.parse(document.getElementById('alvina-catalog').value);
    const branch = document.getElementById('alvina-branch');
    const category = document.getElementById('alvina-category');
    const body = document.getElementById('alvina-products');
    const quantities = new Map();
    const invalidQuantities = new Set();
    function render() {
        body.replaceChildren();
        const products = (catalog[branch.value] || []).filter(p => p.category === category.value);
        document.getElementById('alvina-empty').hidden = products.length !== 0;
        for (const p of products) {
            const row = document.createElement('tr');
            for (const text of [p.name, p.uom]) {
                const cell = document.createElement('td'); cell.textContent = text; row.append(cell);
            }
            const cell = document.createElement('td');
            const input = document.createElement('input');
            input.type = 'number'; input.min = '0'; input.step = String(p.rounding);
            input.value = invalidQuantities.has(p.id) ? '' : quantities.get(p.id) || '0';
            input.className = 'form-control';
            input.setCustomValidity(invalidQuantities.has(p.id) ? 'Ingrese una cantidad válida.' : '');
            input.setAttribute('aria-label', `Cantidad: ${p.name}`);
            input.addEventListener('input', () => {
                input.setCustomValidity('');
                if (input.validity.badInput) {
                    // Browsers also expose an empty value for incomplete numbers.
                    // Keep that error when another category hides this input.
                    invalidQuantities.add(p.id);
                    quantities.set(p.id, input.value);
                } else {
                    invalidQuantities.delete(p.id);
                    if (input.value === '') quantities.delete(p.id);
                    else quantities.set(p.id, input.value);
                }
            });
            cell.append(input); row.append(cell); body.append(row);
        }
    }
    category.addEventListener('change', render);
    branch.addEventListener('change', () => { quantities.clear(); invalidQuantities.clear(); render(); });
    form.addEventListener('submit', event => {
        const lines = Array.from(quantities, ([id, qty]) => ({id, qty}));
        if (invalidQuantities.size || lines.some(p => !Number.isFinite(Number(p.qty)) || Number(p.qty) < 0) ||
            !lines.some(p => Number(p.qty) > 0)) {
            event.preventDefault(); window.alert('Ingrese cantidades válidas y al menos un producto.'); return;
        }
        document.getElementById('alvina-lines').value = JSON.stringify(lines);
        form.querySelector('button[type="submit"]').disabled = true;
    });
    render();
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup);
else setup();
