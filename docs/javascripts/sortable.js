// Click a header of a table.sortable to sort by it; cells carry their sort key in data-sort.
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("table.sortable").forEach(function (table) {
    var body = table.tBodies[0];
    table.querySelectorAll("th").forEach(function (header, column) {
      header.addEventListener("click", function () {
        var numeric = header.dataset.type === "number";
        var descending = header.getAttribute("aria-sort") === "ascending";
        table.querySelectorAll("th").forEach(function (other) { other.removeAttribute("aria-sort"); });
        header.setAttribute("aria-sort", descending ? "descending" : "ascending");
        var rows = Array.prototype.slice.call(body.rows);
        var key = function (row) {
          var value = row.cells[column].dataset.sort || row.cells[column].textContent;
          return numeric ? parseFloat(value) : value.toLowerCase();
        };
        rows.sort(function (a, b) {
          var x = key(a), y = key(b);
          var order = x < y ? -1 : x > y ? 1 : 0;
          return descending ? -order : order;
        });
        rows.forEach(function (row) { body.appendChild(row); });
      });
    });
  });
});
