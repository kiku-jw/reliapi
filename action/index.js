const fs = require('fs');
const https = require('https');
const http = require('http');

function getInput(name, options = {}) {
  const envName = `INPUT_${name.replace(/ /g, '_').replace(/-/g, '_').toUpperCase()}`;
  const value = process.env[envName] || '';

  if (!value && options.required) {
    throw new Error(`Input required and not supplied: ${name}`);
  }

  return value;
}

function appendOutput(name, value) {
  const outputPath = process.env.GITHUB_OUTPUT;
  if (!outputPath) {
    return;
  }

  const stringValue = String(value ?? '');
  if (stringValue.includes('\n')) {
    const delimiter = `EOF_${name}_${Date.now()}`;
    fs.appendFileSync(outputPath, `${name}<<${delimiter}\n${stringValue}\n${delimiter}\n`);
    return;
  }

  fs.appendFileSync(outputPath, `${name}=${stringValue}\n`);
}

const core = {
  getInput,
  info(message) {
    console.log(message);
  },
  warning(message) {
    console.warn(message);
  },
  setOutput(name, value) {
    appendOutput(name, value);
  },
  setFailed(message) {
    console.error(`::error::${message}`);
    process.exitCode = 1;
  }
};

/**
 * Sleep for a given number of milliseconds
 * @param {number} ms - Milliseconds to sleep
 * @returns {Promise<void>}
 */
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Make an HTTP request with retries and exponential backoff
 * @param {Object} options - Request options
 * @returns {Promise<{status: number, body: string, headers: Object}>}
 */
async function makeRequest(options) {
  const {
    url,
    method,
    headers,
    body,
    timeout,
    retries,
    retryDelay
  } = options;

  const parsedUrl = new URL(url);
  const protocol = parsedUrl.protocol === 'https:' ? https : http;

  const requestOptions = {
    hostname: parsedUrl.hostname,
    port: parsedUrl.port || (parsedUrl.protocol === 'https:' ? 443 : 80),
    path: parsedUrl.pathname + parsedUrl.search,
    method: method,
    headers: headers,
    timeout: timeout
  };

  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      core.info(`Attempt ${attempt}/${retries}: ${method} ${url}`);

      const result = await new Promise((resolve, reject) => {
        const req = protocol.request(requestOptions, (res) => {
          let data = '';
          res.on('data', chunk => data += chunk);
          res.on('end', () => {
            resolve({
              status: res.statusCode,
              body: data,
              headers: res.headers
            });
          });
        });

        req.on('error', reject);
        req.on('timeout', () => {
          req.destroy();
          reject(new Error('Request timeout'));
        });

        if (body) {
          req.write(body);
        }
        req.end();
      });

      // Check if we should retry based on status code
      if (result.status >= 500 && attempt < retries) {
        core.warning(`Server error (${result.status}), retrying...`);
        await sleep(retryDelay * attempt); // Exponential backoff
        continue;
      }

      // Retry on 429 (rate limit)
      if (result.status === 429 && attempt < retries) {
        const retryAfter = result.headers['retry-after'];
        const waitTime = retryAfter ? parseInt(retryAfter, 10) * 1000 : retryDelay * attempt;
        core.warning(`Rate limited (429), waiting ${waitTime}ms before retry...`);
        await sleep(waitTime);
        continue;
      }

      return result;

    } catch (error) {
      core.warning(`Request failed: ${error.message}`);

      if (attempt < retries) {
        core.info(`Retrying in ${retryDelay * attempt}ms...`);
        await sleep(retryDelay * attempt);
      } else {
        throw error;
      }
    }
  }

  throw new Error('Max retries exceeded');
}

/**
 * Main action entry point
 */
async function run() {
  try {
    // Get inputs
    const apiUrl = core.getInput('api-url', { required: true });
    const apiKey = core.getInput('api-key');
    const endpoint = core.getInput('endpoint', { required: true });
    const method = core.getInput('method').toUpperCase();
    const body = core.getInput('body');
    const timeout = parseInt(core.getInput('timeout'), 10);
    const retries = parseInt(core.getInput('retries'), 10);
    const retryDelay = parseInt(core.getInput('retry-delay'), 10);

    // Build URL
    const url = `${apiUrl.replace(/\/$/, '')}${endpoint}`;

    // Build headers
    const headers = {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      'User-Agent': 'ReliAPI-GitHub-Action/1.0'
    };

    if (apiKey) {
      headers['X-API-Key'] = apiKey;
    }

    // Make request
    core.info(`Making ${method} request to ${url}`);

    const result = await makeRequest({
      url,
      method,
      headers,
      body: body || undefined,
      timeout,
      retries,
      retryDelay
    });

    // Set outputs
    core.setOutput('response', result.body);
    core.setOutput('status', result.status.toString());
    core.setOutput('success', (result.status >= 200 && result.status < 300).toString());

    // Extract ReliAPI-specific headers
    if (result.headers['x-request-id']) {
      core.setOutput('request-id', result.headers['x-request-id']);
    }
    if (result.headers['x-cache-hit']) {
      core.setOutput('cache-hit', result.headers['x-cache-hit']);
    }
    if (result.headers['x-duration-ms']) {
      core.setOutput('duration-ms', result.headers['x-duration-ms']);
    }

    // Log result
    if (result.status >= 200 && result.status < 300) {
      core.info(`Request successful (${result.status})`);

      // Try to parse and log response
      try {
        const parsed = JSON.parse(result.body);
        core.info(`Response: ${JSON.stringify(parsed, null, 2)}`);
      } catch {
        core.info(`Response: ${result.body}`);
      }
    } else {
      core.warning(`Request returned status ${result.status}`);
      core.warning(`Response: ${result.body}`);
    }

  } catch (error) {
    core.setOutput('success', 'false');
    core.setFailed(`Action failed: ${error.message}`);
  }
}

run();
