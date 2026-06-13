class PaymentService {
    constructor(paymentGateway) {
        this.paymentGateway = paymentGateway;
    }

    async processPayment(userId, amount, currency) {
        if (amount <= 0) {
            throw new Error("Invalid amount");
        }
        const result = await this.paymentGateway.charge(userId, amount, currency);
        return result;
    }

    async refund(transactionId) {
        return await this.paymentGateway.refund(transactionId);
    }

    calculateTotal(items) {
        return items.reduce((sum, item) => sum + item.price, 0);
    }
}

module.exports = PaymentService;
