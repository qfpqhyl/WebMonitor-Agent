'use strict';
// Real React and Vue components render only after the public data request.
(async function () {
  const mount = document.getElementById('app');
  try {
    const response = await fetch('/api/data', {cache: 'no-store'});
    if (!response.ok) throw new Error('Inventory request failed');
    const data = await response.json();
    if (data.private_resource) {
      const script = document.createElement('script');
      script.src = '/assets/private.js';
      document.body.appendChild(script);
    }
    const rowClass = data.selector_changed ? 'replacement-item' : 'inventory-item';
    const limit = data.pagination ? data.page_size : data.items.length;
    function children(h, count, advance) {
      const visible = data.items.slice(0, count);
      return [
        h('p', {id: 'coverage', key: 'coverage'}, data.pagination ? 'Paginated inventory; load all pages to establish full coverage.' : 'Complete inventory'),
        h('ul', {id: 'inventory', key: 'inventory', 'aria-label': 'Inventory items'}, visible.map((item, index) =>
          h('li', {className: rowClass, class: rowClass, key: item.id + ':' + index, 'data-item-id': item.id, 'aria-setsize': data.items.length, 'aria-posinset': index + 1}, [
            h('span', {className: 'item-id', class: 'item-id', key: 'id'}, item.id),
            h('span', {className: 'item-name', class: 'item-name', key: 'name'}, item.name),
            h('span', {className: 'item-price', class: 'item-price', key: 'price'}, item.price),
          ]))),
        h('p', {id: 'page-status', key: 'status', role: 'status'}, 'Showing ' + visible.length + ' of ' + data.items.length + ' items'),
        ...(count < data.items.length ? [h('button', {id: 'next-page', key: 'next', type: 'button', onClick: advance, 'aria-controls': 'inventory'}, 'Load next page')] : []),
      ];
    }
    if (mount.dataset.framework === 'react') {
      function Inventory() {
        const [count, setCount] = React.useState(limit);
        return React.createElement('section', {'aria-label': 'React inventory'}, children(React.createElement, count, () => setCount(value => value + data.page_size)));
      }
      ReactDOM.createRoot(mount).render(React.createElement(Inventory));
    } else {
      Vue.createApp({
        setup() {
          const count = Vue.ref(limit);
          return () => Vue.h('section', {'aria-label': 'Vue inventory'}, children(Vue.h, count.value, () => {count.value += data.page_size;}));
        },
      }).mount(mount);
    }
  } catch (error) {
    mount.replaceChildren();
    const message = document.createElement('p');
    message.id = 'load-error';
    message.setAttribute('role', 'alert');
    message.textContent = 'Unable to load inventory';
    mount.appendChild(message);
  }
}());
