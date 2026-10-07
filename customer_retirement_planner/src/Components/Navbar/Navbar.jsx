import React, { useState } from 'react';
import { NavLink } from 'react-router-dom';
import './Navbar.css';

const Navbar = () => {
  const [mobileMenu, setMobileMenu] = useState(false);
  const toggleMenu = () => setMobileMenu((prev) => !prev);

  const handleMenuKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      toggleMenu();
    }
  };

  const linkClass = ({ isActive }) => `nav-link${isActive ? ' nav-link-active' : ''}`;

  return (
    <nav className="nav-root">
      <div className="nav-inner cont">
        <NavLink to="/" className="brand" aria-label="WealthPlan home">
          <span className="brand-mark" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
              <path
                d="M4 15.5 L9 10.5 L13 13.5 L20 6"
                stroke="white"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <circle cx="20" cy="6" r="2.4" fill="white" />
            </svg>
          </span>
          <span className="brand-name">WealthPlan</span>
        </NavLink>

        <ul className={`nav-links${mobileMenu ? ' open' : ''}`}>
          <li><NavLink to="/" className={linkClass} onClick={() => setMobileMenu(false)}>Home</NavLink></li>
          <li><NavLink to="/Plan" className={linkClass} onClick={() => setMobileMenu(false)}>Plan</NavLink></li>
        </ul>

        <NavLink to="/Plan" className="nav-cta">Get Started</NavLink>

        <button
          type="button"
          className="nav-burger"
          onClick={toggleMenu}
          onKeyDown={handleMenuKeyDown}
          aria-label="Toggle navigation menu"
          aria-expanded={mobileMenu}
        >
          <span /><span /><span />
        </button>
      </div>
    </nav>
  );
};

export default Navbar;
