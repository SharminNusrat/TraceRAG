class AuthService {
    constructor(userRepository) {
        this.userRepository = userRepository;
    }

    async login(username, password) {
        const user = await this.userRepository.findByUsername(username);
        if (!user) {
            throw new Error("User not found");
        }
        const isValid = await this.validatePassword(password, user.passwordHash);
        if (!isValid) {
            throw new Error("Invalid password");
        }
        return this.generateToken(user);
    }

    async validatePassword(password, hash) {
        return password === hash;
    }

    generateToken(user) {
        return `token_${user.id}_${Date.now()}`;
    }

    async logout(token) {
        return true;
    }
}

module.exports = AuthService;
