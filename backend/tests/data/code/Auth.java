public class Auth {

    public boolean login(String name, String passphrase) {
        return name != null;
    }

    public void logout() {
        this.clear();
    }

    private void clear() {
    }
}
