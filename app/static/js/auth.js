/**
 * Authentication helper functions for 3D Model Viewer
 * Este archivo maneja todas las operaciones relacionadas con la autenticación
 * y garantiza que el token JWT se utilice correctamente en toda la aplicación
 */

// Autenticación y manejo de tokens
const Auth = {
    // Verificar si el usuario está logueado
    isAuthenticated: function() {
        const token = localStorage.getItem('token');
        console.log("isAuthenticated check:", token ? "Token exists" : "No token found");
        return token !== null && token !== undefined && token.length > 10;
    },
    
    // Obtener el token actual
    getToken: function() {
        const token = localStorage.getItem('token');
        if (token && token.length > 15) {
            console.log("getToken:", token.substring(0, 15) + "...");
        } else {
            console.log("getToken: No valid token found");
        }
        return token;
    },
    
    // Guardar token en localStorage y como cookie
    setToken: function(token) {
        if (token && token.length > 15) {
            console.log("Setting token:", token.substring(0, 15) + "...");
            localStorage.setItem('token', token);
            
            // También establecer el token como cookie
            this.setCookie('token', token, 7); // Cookie válida por 7 días
            console.log("Token set as cookie");
        } else {
            console.error("Attempted to set invalid token");
        }
    },
    
    // Establecer una cookie
    setCookie: function(name, value, days) {
        let expires = "";
        if (days) {
            const date = new Date();
            date.setTime(date.getTime() + (days * 24 * 60 * 60 * 1000));
            expires = "; expires=" + date.toUTCString();
        }
        document.cookie = name + "=" + encodeURIComponent(value) + expires + "; path=/; SameSite=Strict";
    },
    
    // Obtener una cookie por nombre
    getCookie: function(name) {
        const nameEQ = name + "=";
        const ca = document.cookie.split(';');
        for(let i = 0; i < ca.length; i++) {
            let c = ca[i];
            while (c.charAt(0) === ' ') c = c.substring(1, c.length);
            if (c.indexOf(nameEQ) === 0) {
                return decodeURIComponent(c.substring(nameEQ.length, c.length));
            }
        }
        return null;
    },
    
    // Eliminar una cookie
    deleteCookie: function(name) {
        this.setCookie(name, "", -1);
    },
    
    // Eliminar token (logout)
    removeToken: function() {
        console.log("Token removed from storage");
        localStorage.removeItem('token');
        this.deleteCookie('token');
    },
    
    // Verificar si el token es válido llamando a la API
    validateToken: async function() {
        try {
            const token = this.getToken();
            if (!token || token.length < 10) {
                console.error("No valid token to validate");
                return false;
            }
            
            console.log("Validating token with API...");
            const response = await fetch('/api/v1/auth/test-token', {
                method: 'GET',
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            
            if (!response.ok) {
                console.error('Token validation failed:', response.status, response.statusText);
                const errorText = await response.text();
                console.error('Error response:', errorText);
                // Si el token no es válido, eliminarlo
                this.removeToken();
                return false;
            }
            
            try {
                const data = await response.json();
                console.log('Token validation successful:', data);
                if (!data || !data.username) {
                    console.error('Invalid response data from token validation');
                    this.removeToken();
                    return false;
                }
                return true;
            } catch (jsonError) {
                console.error('Error parsing token validation response:', jsonError);
                this.removeToken();
                return false;
            }
        } catch (error) {
            console.error('Error validating token:', error);
            this.removeToken();
            return false;
        }
    },
    
    // Iniciar sesión y obtener token
    login: async function(username, password) {
        console.log(`Attempting login for user: ${username}`);
        
        // Usar URLSearchParams en lugar de FormData para compatibilidad con OAuth2
        const formData = new URLSearchParams();
        formData.append('username', username);
        formData.append('password', password);
        
        console.log("Sending login request...");
        const response = await fetch('/api/v1/auth/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: formData
        });
        
        console.log("Login response status:", response.status);
        
        if (!response.ok) {
            const errorText = await response.text();
            console.error("Login failed:", errorText);
            throw new Error('Credenciales inválidas');
        }
        
        const data = await response.json();
        console.log("Login successful, received token data:", data);
        
        if (!data.access_token) {
            console.error("No access_token in response");
            throw new Error('Respuesta de token inválida');
        }
        
        // Establecer token en localStorage y como cookie
        this.setToken(data.access_token);
        
        // Verificar inmediatamente que el token se guardó correctamente
        const storedToken = this.getToken();
        if (!storedToken) {
            console.error("Failed to store token in localStorage");
        }
        
        return data;
    },
    
    // Cerrar sesión
    logout: function() {
        this.removeToken();
        window.location.href = '/login';
    },
    
    // Obtener información del usuario actual
    getCurrentUser: async function() {
        try {
            console.log("Getting current user info...");
            const token = this.getToken();
            if (!token || token.length < 10) {
                console.error("No valid token available for getCurrentUser");
                return null;
            }
            
            const response = await fetch('/api/v1/auth/me', {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });
            
            if (!response.ok) {
                console.error('Failed to get current user:', response.status);
                if (response.status === 401) {
                    const errorText = await response.text();
                    console.error('Auth failed in getCurrentUser:', errorText);
                    this.removeToken();
                    return null;
                }
                throw new Error('Failed to fetch user info');
            }
            
            const userData = await response.json();
            console.log("Current user data:", userData);
            return userData;
        } catch (error) {
            console.error('Error fetching current user:', error);
            return null;
        }
    },
    
    // Verificar si el usuario es administrador
    isAdmin: async function() {
        const user = await this.getCurrentUser();
        return user && user.is_admin;
    },
    
    // Proteger rutas - redirigir a login si el usuario no está autenticado
    // Esta función debe ser llamada al principio de cada página protegida
    protectRoute: async function() {
        // Si no estamos en la página de login y no hay token, redirigir a login
        if (window.location.pathname !== '/login' && !this.isAuthenticated()) {
            console.log('No token found, redirecting to login');
            window.location.href = '/login';
            return false;
        }
        
        // Si estamos en una página protegida, verificar que el token sea válido
        if (window.location.pathname !== '/login' && this.isAuthenticated()) {
            console.log('Validating token for protected route');
            const isValid = await this.validateToken();
            if (!isValid) {
                console.log('Token validation failed, redirecting to login');
                window.location.href = '/login';
                return false;
            }
        }
        
        return true;
    },
    
    // API fetch con autenticación incluida
    // Esta función es un wrapper de fetch que agrega el token automáticamente
    fetch: async function(url, options = {}) {
        if (!options.headers) {
            options.headers = {};
        }
        
        const token = this.getToken();
        if (token && token.length > 10) {
            console.log(`Adding auth header to ${url}`);
            options.headers['Authorization'] = `Bearer ${token}`;
        } else {
            console.warn(`No valid token available for ${url}`);
        }
        
        const response = await fetch(url, options);
        
        // Si recibimos un 401, es probable que el token haya expirado
        if (response.status === 401 && window.location.pathname !== '/login') {
            console.error(`Unauthorized response from API: ${url}`);
            this.removeToken();
            window.location.href = '/login';
            return null;
        }
        
        return response;
    }
};