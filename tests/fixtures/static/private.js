'use strict';
(function () {
  const targets = ['http://127.0.0.1/', 'http://169.254.169.254/latest/meta-data/', 'http://[::1]/'];
  let finished = 0;
  for (const target of targets) {
    const image = document.createElement('img');
    image.alt = 'Private network image probe (must be blocked)';
    image.width = 1;
    image.height = 1;
    image.src = target + 'smoke-private-image';
    document.body.appendChild(image);
    fetch(target, {mode: 'no-cors', signal: AbortSignal.timeout(5000)})
      .then(() => record('unexpected-resolution'))
      .catch(() => record('blocked-or-failed'));
  }
  function record(outcome) {
    finished += 1;
    const status = document.getElementById('probe-status');
    if (status) {
      status.dataset.lastOutcome = outcome;
      status.textContent = 'Completed ' + finished + ' of 3 private fetch probes; consult collector evidence for proxy denial.';
    }
  }
}());
