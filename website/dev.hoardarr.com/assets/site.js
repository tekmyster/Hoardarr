(() => {
  const root = document.documentElement;
  const savedTheme = localStorage.getItem("hoardarr-theme");
  if (savedTheme === "light" || savedTheme === "dark") root.dataset.theme = savedTheme;

  const themeButton = document.querySelector("[data-theme-toggle]");
  if (themeButton) {
    const syncLabel = () => {
      const next = root.dataset.theme === "light" ? "dark" : "light";
      themeButton.setAttribute("aria-label", `Use ${next} theme`);
      themeButton.textContent = next === "dark" ? "◐" : "☀";
    };
    syncLabel();
    themeButton.addEventListener("click", () => {
      root.dataset.theme = root.dataset.theme === "light" ? "dark" : "light";
      localStorage.setItem("hoardarr-theme", root.dataset.theme);
      syncLabel();
    });
  }

  const menuButton = document.querySelector("[data-menu-toggle]");
  const nav = document.querySelector("[data-nav]");
  if (menuButton && nav) {
    menuButton.addEventListener("click", () => {
      const open = nav.classList.toggle("open");
      menuButton.setAttribute("aria-expanded", String(open));
    });
  }

  const filters = [...document.querySelectorAll("[data-filter]")];
  const features = [...document.querySelectorAll("[data-feature]")];
  filters.forEach((button) => {
    button.addEventListener("click", () => {
      const filter = button.dataset.filter;
      filters.forEach((item) => item.classList.toggle("active", item === button));
      features.forEach((card) => {
        card.hidden = filter !== "all" && card.dataset.feature !== filter;
      });
    });
  });
})();
