class UserService {
    constructor(userRepository) {
        this.userRepository = userRepository;
    }

    async createUser(username, email, password) {
        const existing = await this.userRepository.findByEmail(email);
        if (existing) {
            throw new Error("Email already exists");
        }
        return await this.userRepository.save({ username, email, password });
    }

    async getUserById(userId) {
        return await this.userRepository.findById(userId);
    }

    async updateProfile(userId, data) {
        return await this.userRepository.update(userId, data);
    }

    async deleteUser(userId) {
        return await this.userRepository.delete(userId);
    }
}

module.exports = UserService;
