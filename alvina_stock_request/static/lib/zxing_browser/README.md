# ZXing Browser (local)

- Upstream: <https://github.com/zxing-js/browser>
- npm package: `@zxing/browser@0.1.5`, official UMD minified distribution.
- Source tarball: <https://registry.npmjs.org/@zxing/browser/-/browser-0.1.5.tgz>
- npm integrity, verified before copying:
  `sha512-4Lmrn/il4+UNb87Gk8h1iWnhj39TASEHpd91CwwSJtY5u+wa0iH9qS0wNLAWbNVYXR66WmT5uiMhZ7oVTrKfxw==`
- `zxing-browser.min.js` SHA-256 (unmodified):
  `b5ad3df920738ca7adcb74508d7e6b6a5b9024993fb9a0c702da1ad8964eca07`
- Browser wrapper: MIT; see `LICENSE-MIT`.
- Bundled ZXing decoding algorithms: Apache 2.0, Copyright 2009 ZXing authors;
  see `LICENSE-APACHE-2.0`. The official UMD distribution bundles the decoder;
  no separate npm installation is needed on an Odoo server.

The addon loads this local file through Odoo's `loadJS` on first use. It never
loads a CDN and never sends selected images or camera frames to a server.
No runtime package manager or external decoding service is required.

To replace the dependency, verify the new tarball integrity and copied bundle
hash, preserve both licenses, and rerun the browser decoding and camera
lifecycle tests before updating these values.
