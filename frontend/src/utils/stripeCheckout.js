/**
 * Handle Stripe checkout API responses, including billing-portal redirects for past-due users.
 * @returns {boolean} true if a redirect was started
 */
export function handleStripeCheckoutResponse(response) {
  const data = response?.data?.data;
  if (data?.action === 'billing_portal' && data?.portal_url) {
    window.location.href = data.portal_url;
    return true;
  }
  if (response?.data?.status === 'success' && data?.checkout_url) {
    window.location.href = data.checkout_url;
    return true;
  }
  return false;
}

/**
 * @returns {{ message: string, redirected: boolean }}
 */
export function handleStripeCheckoutError(err) {
  const errData = err?.response?.data?.data;
  if (errData?.action === 'billing_portal' && errData?.portal_url) {
    window.location.href = errData.portal_url;
    return { message: err?.response?.data?.message || '', redirected: true };
  }
  const message =
    err?.response?.data?.message ||
    err?.response?.data?.detail ||
    err?.message ||
    'Failed to start checkout';
  return { message, redirected: false };
}
