
#ifndef RATHENA_PASSWORD_HPP
#define RATHENA_PASSWORD_HPP

#include <cstddef>

namespace password {

    // Generate an Argon2id password hash.
    // Returns true when hashing succeeds.
    bool hash_argon2id(
        const char* plaintext,
        char* encoded_hash,
        std::size_t encoded_hash_capacity
    );

    // Verify a plaintext password against an encoded Argon2id hash.
    // Returns true only when the password matches.
    bool verify_argon2id(
        const char* plaintext,
        const char* encoded_hash
    );

}

#endif // RATHENA_PASSWORD_HPP
