const discountCountdowns = document.querySelectorAll("[data-discount-ends-at]");

const updateDiscountCountdowns = () => {
  discountCountdowns.forEach((element) => {
    const remaining = new Date(element.dataset.discountEndsAt).getTime() - Date.now();
    if (remaining <= 0) {
      element.textContent = "Discount ended — updating price…";
      window.location.reload();
      return;
    }
    const totalSeconds = Math.floor(remaining / 1000);
    const days = Math.floor(totalSeconds / 86400);
    const hours = Math.floor((totalSeconds % 86400) / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    element.textContent = `Discount remaining: ${days}d ${hours}h ${minutes}m ${seconds}s`;
  });
};

if (discountCountdowns.length) {
  updateDiscountCountdowns();
  setInterval(updateDiscountCountdowns, 1000);
}
