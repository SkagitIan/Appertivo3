document.addEventListener("DOMContentLoaded", () => {
  const sidebar = document.querySelector(".admin-sidebar");
  const toggle = document.querySelector("[data-sidebar-toggle]");
  const groups = document.querySelectorAll(".group-toggle");
  const storageKey = "appertivo-admin-sidebar";

  const savedState = (() => {
    try {
      const raw = window.localStorage.getItem(storageKey);
      return raw ? JSON.parse(raw) : {};
    } catch (error) {
      console.warn("Unable to read sidebar state", error);
      return {};
    }
  })();

  groups.forEach((group, index) => {
    const groupId = `group-${index}`;
    const list = document.getElementById(`sidebar-group-${index}`);
    const initialOpen = savedState[groupId] !== false;
    list.style.display = initialOpen ? "block" : "none";
    group.setAttribute("aria-expanded", initialOpen);

    group.addEventListener("click", () => {
      const isExpanded = group.getAttribute("aria-expanded") === "true";
      const nextState = !isExpanded;
      group.setAttribute("aria-expanded", String(nextState));
      list.style.display = nextState ? "block" : "none";
      savedState[groupId] = nextState;
      try {
        window.localStorage.setItem(storageKey, JSON.stringify(savedState));
      } catch (error) {
        console.warn("Unable to persist sidebar state", error);
      }
    });
  });

  if (toggle && sidebar) {
    toggle.addEventListener("click", () => {
      const isOpen = sidebar.getAttribute("data-open") === "true";
      sidebar.setAttribute("data-open", String(!isOpen));
    });
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && sidebar?.getAttribute("data-open") === "true") {
      sidebar.setAttribute("data-open", "false");
    }
  });

  const statusCells = document.querySelectorAll("td.field-status");
  statusCells.forEach((cell) => {
    const value = cell.textContent.trim();
    if (!value) {
      return;
    }
    const normalized = value.toLowerCase();
    let badgeClass = "badge-neutral";
    if (["succeeded", "active", "complete", "completed", "ready"].includes(normalized)) {
      badgeClass = "badge-success";
    } else if (["running", "in_progress", "processing"].includes(normalized)) {
      badgeClass = "badge-info";
    } else if (["failed", "inactive", "error", "cancelled"].includes(normalized)) {
      badgeClass = "badge-danger";
    } else if (["queued", "pending", "draft"].includes(normalized)) {
      badgeClass = "badge-warning";
    }

    cell.innerHTML = `<span class="status-pill ${badgeClass}">${value}</span>`;
  });
});

