document.documentElement.classList.add("js");

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
