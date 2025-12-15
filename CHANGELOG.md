# [2.0.0](https://github.com/arvatoaws-labs/automatic-zone-placement/compare/v1.3.0...v2.0.0) (2025-12-15)


* feat!: enforce strict API paths, add IP lookup, and validate FQDNs ([95305f2](https://github.com/arvatoaws-labs/automatic-zone-placement/commit/95305f2aa8347bdc0b5f6aba3f51076b39a52a0b))


### BREAKING CHANGES

* The service no longer accepts lookups at the root path (`/`). Clients must update to use `/fqdn/<hostname>`.

# [1.3.0](https://github.com/arvatoaws-labs/automatic-zone-placement/compare/v1.2.0...v1.3.0) (2025-12-12)


### Features

* prevent duplicate zone placement and add policy tests ([3560ff6](https://github.com/arvatoaws-labs/automatic-zone-placement/commit/3560ff674cccfc84872ae5cd178f1a39d2f14bdd))
* prevent duplicate zone placement and add policy tests ([b32c14c](https://github.com/arvatoaws-labs/automatic-zone-placement/commit/b32c14c5b68241987d0a1a0e84fd0a0a0c67c3d0))

# [1.2.0](https://github.com/arvatoaws-labs/automatic-zone-placement/compare/v1.1.0...v1.2.0) (2025-12-12)


### Features

* added topologySpreadConstraints and cleaned up policy ([0d5ab62](https://github.com/arvatoaws-labs/automatic-zone-placement/commit/0d5ab62bcb69ac78c080bbb9142ca8a0648e8c2b))

# [1.1.0](https://github.com/arvatoaws-labs/automatic-zone-placement/compare/v1.0.0...v1.1.0) (2025-12-08)


### Bug Fixes

* **helm:** hardcode PORT environment variable in deployment ([67f65ee](https://github.com/arvatoaws-labs/automatic-zone-placement/commit/67f65ee9ee0628a096e9a167a26bd8e3b6495bba))
* updated helm kyverno policy ([e4db622](https://github.com/arvatoaws-labs/automatic-zone-placement/commit/e4db622d6cb3af530402d0035b20c6607e341102))


### Features

* add Prometheus metrics, caching improvements, and monitoring support ([99e4abb](https://github.com/arvatoaws-labs/automatic-zone-placement/commit/99e4abb3d38c77aad8cb366a6beaf0fc155a6ea3))

# 1.0.0 (2025-12-08)


### Bug Fixes

* add link ([cda0d3a](https://github.com/arvatoaws-labs/automatic-zone-placement/commit/cda0d3af8b2df6956320bbcc5f1247690ce71950))
* improve container security ([77c60da](https://github.com/arvatoaws-labs/automatic-zone-placement/commit/77c60da7e5e6ef65790ecfa823e0bcd70cc80b1f))
* use correct port in code to match docs ([ed51f71](https://github.com/arvatoaws-labs/automatic-zone-placement/commit/ed51f716bd4529ee6f35a73e7398dbf19853555b))


### Features

* add development tooling and improve configuration flexibility ([8ed37f8](https://github.com/arvatoaws-labs/automatic-zone-placement/commit/8ed37f840123c6b3e2bfab593d6b9c166f3ec702))
* add production-ready deployment configuration ([923c7d3](https://github.com/arvatoaws-labs/automatic-zone-placement/commit/923c7d354ed2d36749010b3ecaf7fef031274714))
