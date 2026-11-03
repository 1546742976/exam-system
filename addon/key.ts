const publicKey = `-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAsznn52BKVBl6bQcacK6I
B9ZaK448uyxSBUOzok2hwsfZ/SBU0RQcfKD5ponDN5pmk0MnT041gDhGImb87NlG
6DeUvc/I6Hr8NVgNfeZHvsoExQhDUZTH2n++pP4E0yxpcs0hxxDoyFpHxwawnYe/
Du5tSPgrNiDnM3I6MNbhTmJRnp9xdAyg5rt5H5kyjUefmHhuARfz3pDQWxF6+is/
dhfpgWkzkC3Hk5Hnikj4e29GETuER4y/nt3yg1yL61BtQvyGZbiS1lwa1pCjk2p5
lVe0qEYYvFfbAbDV3TCCbsMhl4rJBEHZD+o0S+9HMFNWHSK8ItXoOE0GIl7froYw
VQIDAQAB
-----END PUBLIC KEY-----`;

// 私钥不再写死在源码中。请在插件配置 exam.privateKeyFile 或环境变量
// CKYF_EXAM_PRIVATE_KEY_FILE 中指向 server/keys/private.pem。

export { publicKey };
