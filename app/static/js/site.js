document.documentElement.classList.add("js");

const menuToggle = document.querySelector(".menu-toggle");
const siteMenu = document.querySelector("#site-menu");
const menuOverlay = document.querySelector(".sidebar-overlay");
const closeMenu = () => {
  menuToggle?.setAttribute("aria-expanded", "false");
  menuToggle?.setAttribute("aria-label", "Open menu");
  siteMenu?.classList.remove("is-open");
  siteMenu?.setAttribute("aria-hidden", "true");
  menuOverlay?.classList.remove("is-open");
  document.body.classList.remove("menu-open");
};
menuToggle?.addEventListener("click", () => {
  const isOpen = menuToggle.getAttribute("aria-expanded") === "true";
  if (isOpen) return closeMenu();
  menuToggle.setAttribute("aria-expanded", "true");
  menuToggle.setAttribute("aria-label", "Close menu");
  siteMenu?.classList.add("is-open");
  siteMenu?.setAttribute("aria-hidden", "false");
  menuOverlay?.classList.add("is-open");
  document.body.classList.add("menu-open");
});
menuOverlay?.addEventListener("click", closeMenu);
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeMenu(); });
siteMenu?.querySelectorAll("a").forEach((link) => link.addEventListener("click", closeMenu));

const pdfInput = document.querySelector("#pdf");
const titleInput = document.querySelector("#title");

pdfInput?.addEventListener("change", () => {
  const fileName = pdfInput.files?.[0]?.name;
  if (!fileName || !titleInput || titleInput.value.trim()) return;
  titleInput.value = fileName.replace(/\.pdf$/i, "");
});

document.querySelector("[data-copy-url]")?.addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const status = document.querySelector("#copy-status");
  try {
    await navigator.clipboard.writeText(button.dataset.copyUrl);
    status.textContent = "Link copied.";
    button.textContent = "Copied";
  } catch {
    const input = document.querySelector("#share-url");
    input?.select();
    document.execCommand("copy");
    status.textContent = "Link copied.";
  }
});

const checkoutDialog = document.querySelector("#checkout-dialog");
document.querySelector("[data-open-checkout]")?.addEventListener("click", () => {
  checkoutDialog?.showModal();
  document.body.classList.add("dialog-open");
});
document.querySelector("[data-close-checkout]")?.addEventListener("click", () => {
  checkoutDialog?.close();
});
checkoutDialog?.addEventListener("click", (event) => {
  if (event.target === checkoutDialog) checkoutDialog.close();
});
checkoutDialog?.addEventListener("close", () => {
  document.body.classList.remove("dialog-open");
});
