const paymentForm = document.querySelector("#mpesa-payment-form");

if (paymentForm) {
  const submitButton = paymentForm.querySelector("button[type='submit']");
  const phoneInput = paymentForm.querySelector("input[name='phone_number']");
  const message = document.querySelector("#payment-feedback");
  let pollingTimer;

  const setMessage = (text, state = "") => {
    message.textContent = text;
    message.className = `mpesa-message ${state}`;
  };

  const pollStatus = async () => {
    try {
      const response = await fetch("/payments/mpesa/status", { credentials: "same-origin" });
      const data = await response.json();
      if (data.status === "paid") {
        clearInterval(pollingTimer);
        setMessage("Payment received. Your download is ready.", "success");
        submitButton.replaceWith(Object.assign(document.createElement("a"), {
          className: "mpesa-submit download-ready", href: data.download_url, textContent: "Download your e-book"
        }));
      } else if (data.status === "failed") {
        clearInterval(pollingTimer);
        submitButton.disabled = false;
        phoneInput.disabled = false;
        submitButton.textContent = "Send M-Pesa prompt";
        setMessage("The M-Pesa payment was not completed. You can try again.", "error");
      }
    } catch {
      // A transient polling failure should not interrupt the user's M-Pesa prompt.
    }
  };

  paymentForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const phoneNumber = phoneInput.value;
    submitButton.disabled = true;
    phoneInput.disabled = true;
    submitButton.innerHTML = '<span class="loading-spinner" aria-hidden="true"></span>Sending M-Pesa prompt…';
    setMessage("Contacting M-Pesa. Please wait…");
    try {
      const response = await fetch(paymentForm.action, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone_number: phoneNumber })
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || "Unable to start payment.");
      submitButton.innerHTML = '<span class="loading-spinner" aria-hidden="true"></span>Waiting for payment…';
      setMessage(data.message, "success");
      pollingTimer = setInterval(pollStatus, 3000);
      pollStatus();
    } catch (error) {
      submitButton.disabled = false;
      phoneInput.disabled = false;
      submitButton.textContent = "Send M-Pesa prompt";
      setMessage(error.message, "error");
    }
  });
}
