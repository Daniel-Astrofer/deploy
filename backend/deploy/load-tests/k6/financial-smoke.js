import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter } from 'k6/metrics';

const BASE_URL = (__ENV.BASE_URL || 'http://localhost:8080').replace(/\/$/, '');
const JWT = __ENV.JWT || '';
const ENABLE_WRITES = (__ENV.ENABLE_WRITES || 'false').toLowerCase() === 'true';
const IDEMPOTENCY_PREFIX = __ENV.IDEMPOTENCY_PREFIX || `k6-${Date.now()}`;
const PROFILE = (__ENV.PROFILE || 'smoke').toLowerCase();

const authFailures = new Counter('kerosene_auth_failures');
const financialValidationFailures = new Counter('kerosene_financial_validation_failures');
const idempotencyConflicts = new Counter('kerosene_idempotency_conflicts');

export const options = {
  scenarios: buildScenarios(),
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<750', 'p(99)<1500'],
    kerosene_auth_failures: ['count<1'],
    kerosene_financial_validation_failures: ['count<1'],
  },
};

function buildScenarios() {
  if (PROFILE === '1m_day') {
    return {
      read_mix_1m_day: {
        executor: 'constant-arrival-rate',
        rate: Number(__ENV.RATE || 12),
        timeUnit: '1s',
        duration: __ENV.DURATION || '24h',
        preAllocatedVUs: Number(__ENV.PREALLOCATED_VUS || 50),
        maxVUs: Number(__ENV.MAX_VUS || 250),
        exec: 'readMix',
      },
      ledger_write_probe: {
        executor: 'constant-arrival-rate',
        rate: Number(__ENV.WRITE_RATE || 1),
        timeUnit: '1s',
        duration: __ENV.WRITE_DURATION || '10m',
        preAllocatedVUs: Number(__ENV.WRITE_PREALLOCATED_VUS || 5),
        maxVUs: Number(__ENV.WRITE_MAX_VUS || 25),
        exec: 'ledgerWrite',
      },
    };
  }

  if (PROFILE === '1m_hour') {
    return {
      read_mix_1m_hour: {
        executor: 'constant-arrival-rate',
        rate: Number(__ENV.RATE || 278),
        timeUnit: '1s',
        duration: __ENV.DURATION || '1h',
        preAllocatedVUs: Number(__ENV.PREALLOCATED_VUS || 500),
        maxVUs: Number(__ENV.MAX_VUS || 2000),
        exec: 'readMix',
      },
      ledger_write_probe: {
        executor: 'constant-arrival-rate',
        rate: Number(__ENV.WRITE_RATE || 5),
        timeUnit: '1s',
        duration: __ENV.WRITE_DURATION || '10m',
        preAllocatedVUs: Number(__ENV.WRITE_PREALLOCATED_VUS || 50),
        maxVUs: Number(__ENV.WRITE_MAX_VUS || 250),
        exec: 'ledgerWrite',
      },
    };
  }

  if (PROFILE === 'concurrency') {
    return {
      concurrent_read_mix: {
        executor: 'constant-vus',
        vus: Number(__ENV.VUS || 1000),
        duration: __ENV.DURATION || '10m',
        exec: 'readMix',
      },
    };
  }

  return {
    auth_login: {
      executor: 'constant-vus',
      vus: Number(__ENV.AUTH_VUS || 1),
      duration: __ENV.AUTH_DURATION || '30s',
      exec: 'authLogin',
    },
    wallet_read: {
      executor: 'constant-vus',
      vus: Number(__ENV.WALLET_READ_VUS || 2),
      duration: __ENV.WALLET_READ_DURATION || '30s',
      exec: 'walletRead',
    },
    ledger_read: {
      executor: 'constant-vus',
      vus: Number(__ENV.LEDGER_READ_VUS || 2),
      duration: __ENV.LEDGER_READ_DURATION || '30s',
      exec: 'ledgerRead',
    },
    transaction_status: {
      executor: 'constant-vus',
      vus: Number(__ENV.TX_STATUS_VUS || 1),
      duration: __ENV.TX_STATUS_DURATION || '30s',
      exec: 'transactionStatus',
    },
    ledger_write: {
      executor: 'constant-vus',
      vus: Number(__ENV.LEDGER_WRITE_VUS || 1),
      duration: __ENV.LEDGER_WRITE_DURATION || '30s',
      exec: 'ledgerWrite',
    },
  };
}

function jsonHeaders(extra = {}) {
  return {
    headers: {
      'Content-Type': 'application/json',
      ...(JWT ? { Authorization: `Bearer ${JWT}` } : {}),
      ...extra,
    },
  };
}

function requireJwt() {
  return JWT.length > 0;
}

function idempotencyKey(scope) {
  return `${IDEMPOTENCY_PREFIX}-${scope}-${__VU}-${__ITER}`;
}

export function authLogin() {
  const username = __ENV.LOGIN_USERNAME;
  const password = __ENV.LOGIN_PASSWORD || __ENV.LOGIN_PASSPHRASE;
  if (!username || !password) {
    sleep(1);
    return;
  }

  const response = http.post(
    `${BASE_URL}/auth/login`,
    JSON.stringify({ username, password }),
    jsonHeaders(),
  );
  const ok = check(response, {
    'login accepted': (r) => r.status === 202 || r.status === 200,
  });
  if (!ok) authFailures.add(1);

  const totpCode = __ENV.LOGIN_TOTP_CODE;
  if (totpCode && response.status >= 200 && response.status < 300) {
    const preAuthToken = response.json('data');
    const verify = http.post(
      `${BASE_URL}/auth/login/totp/verify`,
      JSON.stringify({ preAuthToken, totpCode }),
      jsonHeaders(),
    );
    const verified = check(verify, {
      'totp verification accepted': (r) => r.status === 202 || r.status === 200,
    });
    if (!verified) authFailures.add(1);
  }

  sleep(1);
}

export function walletRead() {
  if (!requireJwt()) {
    sleep(1);
    return;
  }
  const response = http.get(`${BASE_URL}/wallet/all`, jsonHeaders());
  check(response, {
    'wallet read ok': (r) => r.status === 200,
    'wallet response has no passphraseHash': (r) => !r.body.includes('passphraseHash'),
  });
  sleep(1);
}

export function ledgerRead() {
  if (!requireJwt()) {
    sleep(1);
    return;
  }
  const history = http.get(`${BASE_URL}/ledger/history?page=0&size=20`, jsonHeaders());
  check(history, {
    'ledger history ok': (r) => r.status === 200,
  });

  const walletName = __ENV.WALLET_NAME;
  if (walletName) {
    const balance = http.get(
      `${BASE_URL}/ledger/balance?walletName=${encodeURIComponent(walletName)}`,
      jsonHeaders(),
    );
    check(balance, {
      'ledger balance ok': (r) => r.status === 200,
    });
  }
  sleep(1);
}

export function readMix() {
  walletRead();
  ledgerRead();
  transactionStatus();
  networkTransfersRead();
}

export function transactionStatus() {
  if (!requireJwt()) {
    sleep(1);
    return;
  }
  const txid = __ENV.TXID || '0'.repeat(64);
  const response = http.get(
    `${BASE_URL}/transactions/status?txid=${encodeURIComponent(txid)}`,
    jsonHeaders(),
  );
  check(response, {
    'transaction status handled': (r) => r.status === 200 || r.status === 400 || r.status === 404,
  });
  sleep(1);
}

export function networkTransfersRead() {
  if (!requireJwt()) {
    sleep(1);
    return;
  }
  const response = http.get(`${BASE_URL}/transactions/network/transfers`, jsonHeaders());
  check(response, {
    'network transfers read ok': (r) => r.status === 200,
  });
  sleep(1);
}

export function ledgerWrite() {
  if (!requireJwt() || !ENABLE_WRITES) {
    sleep(1);
    return;
  }

  const sender = __ENV.WALLET_SENDER;
  const receiver = __ENV.WALLET_RECEIVER;
  const amount = __ENV.LEDGER_AMOUNT || '0.00000001';
  if (!sender || !receiver) {
    financialValidationFailures.add(1);
    sleep(1);
    return;
  }

  const response = http.post(
    `${BASE_URL}/ledger/transaction`,
    JSON.stringify({
      sender,
      receiver,
      amount,
      context: 'k6-financial-smoke',
      idempotencyKey: idempotencyKey('ledger'),
      requestTimestamp: Date.now(),
    }),
    jsonHeaders(),
  );

  check(response, {
    'ledger write accepted or business-rejected': (r) => [200, 202, 400, 402, 403, 404, 409, 422].includes(r.status),
    'ledger write does not accept invalid amount': (r) => r.status !== 500,
  });
  if (response.status === 409) idempotencyConflicts.add(1);
  if (response.status === 500) financialValidationFailures.add(1);

  sleep(1);
}
