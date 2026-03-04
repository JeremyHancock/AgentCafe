/**
 * WebAuthn passkey helpers for AgentCafe.
 *
 * Handles base64url encoding/decoding and the browser-side WebAuthn ceremony
 * for registration and login. No external dependencies.
 */

/* -----------------------------------------------------------------------
 * base64url helpers
 * ----------------------------------------------------------------------- */

function base64urlToBytes(base64url) {
    const base64 = base64url.replace(/-/g, '+').replace(/_/g, '/');
    const pad = base64.length % 4 === 0 ? '' : '='.repeat(4 - (base64.length % 4));
    const binary = atob(base64 + pad);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
}

function bytesToBase64url(bytes) {
    const binary = String.fromCharCode(...new Uint8Array(bytes));
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/* -----------------------------------------------------------------------
 * Credential serialization (browser → server)
 * ----------------------------------------------------------------------- */

function serializeRegistrationCredential(credential) {
    return {
        id: credential.id,
        rawId: bytesToBase64url(credential.rawId),
        type: credential.type,
        response: {
            attestationObject: bytesToBase64url(credential.response.attestationObject),
            clientDataJSON: bytesToBase64url(credential.response.clientDataJSON),
        },
    };
}

function serializeAuthenticationCredential(credential) {
    return {
        id: credential.id,
        rawId: bytesToBase64url(credential.rawId),
        type: credential.type,
        response: {
            authenticatorData: bytesToBase64url(credential.response.authenticatorData),
            clientDataJSON: bytesToBase64url(credential.response.clientDataJSON),
            signature: bytesToBase64url(credential.response.signature),
            userHandle: credential.response.userHandle
                ? bytesToBase64url(credential.response.userHandle)
                : null,
        },
    };
}

/* -----------------------------------------------------------------------
 * Convert server options to browser-ready format
 * ----------------------------------------------------------------------- */

function prepareRegistrationOptions(options) {
    const prepared = { ...options };
    prepared.challenge = base64urlToBytes(options.challenge);
    prepared.user = {
        ...options.user,
        id: base64urlToBytes(options.user.id),
    };
    if (options.excludeCredentials) {
        prepared.excludeCredentials = options.excludeCredentials.map(function(c) {
            return { ...c, id: base64urlToBytes(c.id) };
        });
    }
    return prepared;
}

function prepareAuthenticationOptions(options) {
    const prepared = { ...options };
    prepared.challenge = base64urlToBytes(options.challenge);
    if (options.allowCredentials) {
        prepared.allowCredentials = options.allowCredentials.map(function(c) {
            return { ...c, id: base64urlToBytes(c.id) };
        });
    }
    return prepared;
}

/* -----------------------------------------------------------------------
 * High-level flows
 * ----------------------------------------------------------------------- */

/**
 * Register a new passkey.
 * @param {string} email
 * @param {string} displayName
 * @param {string} nextUrl - URL to redirect after success
 * @param {function} onError - called with error message string
 */
async function passkeyRegister(email, displayName, nextUrl, onError) {
    try {
        // 1. Begin — get registration options from server
        const beginResp = await fetch('/human/passkey/register/begin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: email, display_name: displayName || null }),
        });
        if (!beginResp.ok) {
            const err = await beginResp.json();
            onError(err.detail?.message || err.detail?.error || 'Registration failed.');
            return;
        }
        const options = await beginResp.json();
        const challengeId = options.challenge_id;

        // 2. Create credential via browser WebAuthn API
        const publicKey = prepareRegistrationOptions(options);
        let credential;
        try {
            credential = await navigator.credentials.create({ publicKey: publicKey });
        } catch (e) {
            if (e.name === 'NotAllowedError') {
                onError('Passkey registration was cancelled.');
            } else {
                onError('Passkey registration failed: ' + e.message);
            }
            return;
        }

        // 3. Complete — send credential to server for verification
        const completeResp = await fetch('/human/passkey/register/complete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                challenge_id: challengeId,
                credential: serializeRegistrationCredential(credential),
            }),
        });
        if (!completeResp.ok) {
            const err = await completeResp.json();
            onError(err.detail?.message || err.detail?.error || 'Verification failed.');
            return;
        }
        const result = await completeResp.json();

        // 4. Set session cookie via server endpoint
        const sessionResp = await fetch('/auth/session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_token: result.session_token,
                next_url: nextUrl || '/',
            }),
        });
        if (!sessionResp.ok) {
            onError('Failed to establish session.');
            return;
        }
        const sessionData = await sessionResp.json();
        window.location.href = sessionData.redirect;

    } catch (e) {
        onError('An unexpected error occurred: ' + e.message);
    }
}

/**
 * Login with an existing passkey.
 * @param {string|null} email - optional, null for discoverable credential login
 * @param {string} nextUrl - URL to redirect after success
 * @param {function} onError - called with error message string
 */
async function passkeyLogin(email, nextUrl, onError) {
    try {
        // 1. Begin — get authentication options from server
        const beginResp = await fetch('/human/passkey/login/begin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: email || null }),
        });
        if (!beginResp.ok) {
            const err = await beginResp.json();
            onError(err.detail?.message || err.detail?.error || 'Login failed.');
            return;
        }
        const options = await beginResp.json();
        const challengeId = options.challenge_id;

        // 2. Get credential via browser WebAuthn API
        const publicKey = prepareAuthenticationOptions(options);
        let credential;
        try {
            credential = await navigator.credentials.get({ publicKey: publicKey });
        } catch (e) {
            if (e.name === 'NotAllowedError') {
                onError('Passkey login was cancelled.');
            } else {
                onError('Passkey login failed: ' + e.message);
            }
            return;
        }

        // 3. Complete — send assertion to server for verification
        const completeResp = await fetch('/human/passkey/login/complete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                challenge_id: challengeId,
                credential: serializeAuthenticationCredential(credential),
            }),
        });
        if (!completeResp.ok) {
            const err = await completeResp.json();
            onError(err.detail?.message || err.detail?.error || 'Verification failed.');
            return;
        }
        const result = await completeResp.json();

        // 4. Set session cookie via server endpoint
        const sessionResp = await fetch('/auth/session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_token: result.session_token,
                next_url: nextUrl || '/',
            }),
        });
        if (!sessionResp.ok) {
            onError('Failed to establish session.');
            return;
        }
        const sessionData = await sessionResp.json();
        window.location.href = sessionData.redirect;

    } catch (e) {
        onError('An unexpected error occurred: ' + e.message);
    }
}

/**
 * Get a raw passkey assertion (challenge_id + credential) without completing login.
 * Used by consent approval — the assertion is passed to the server which verifies
 * it as part of the approval flow (single code path, single security gate).
 * @param {string|null} email
 * @returns {Promise<{challenge_id: string, credential: object}>}
 */
async function passkeyGetAssertion(email) {
    const beginResp = await fetch('/human/passkey/login/begin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email || null }),
    });
    if (!beginResp.ok) {
        const err = await beginResp.json();
        throw new Error(err.detail?.message || err.detail?.error || 'Re-auth failed.');
    }
    const options = await beginResp.json();
    const challengeId = options.challenge_id;

    const publicKey = prepareAuthenticationOptions(options);
    const credential = await navigator.credentials.get({ publicKey: publicKey });

    return {
        challenge_id: challengeId,
        credential: serializeAuthenticationCredential(credential),
    };
}


/**
 * Re-authenticate with passkey (convenience wrapper for login flows).
 * Resolves with the session token on success, rejects on failure.
 * @param {string|null} email
 * @returns {Promise<string>} session_token
 */
async function passkeyReauth(email) {
    const assertion = await passkeyGetAssertion(email);

    const completeResp = await fetch('/human/passkey/login/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            challenge_id: assertion.challenge_id,
            credential: assertion.credential,
        }),
    });
    if (!completeResp.ok) {
        const err = await completeResp.json();
        throw new Error(err.detail?.message || err.detail?.error || 'Verification failed.');
    }
    const result = await completeResp.json();
    return result.session_token;
}

/**
 * Check if the browser supports WebAuthn passkeys.
 */
function isPasskeySupported() {
    return !!(window.PublicKeyCredential && navigator.credentials && navigator.credentials.create);
}
