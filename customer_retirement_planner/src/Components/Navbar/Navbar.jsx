// Navbar.jsx
import React,{ useState } from 'react';
import { NavLink } from 'react-router-dom'; 
import analytics from '../../Assets/Navbar/analytics.gif';
import wealthPlan from '../../Assets/Navbar/wealthPlan.png';
import user from '../../Assets/Navbar/user.gif';
import notifications from '../../Assets/Navbar/notifications.gif';

import menu from '../../Assets/Navbar/menu.png'

// import { FaCartPlus } from "react-icons/fa";

 import './Navbar.css'

const Navbar = () => {

  const [mobileMenu, setMobileMenu] = useState(false);
  const toggleMenu = () =>{
    mobileMenu ? setMobileMenu(false) : setMobileMenu(true);

  }
  return (
    <nav className='cont dark-nav'>
      <img src={analytics} alt='logo' className='logo'/>
      <img src={wealthPlan} alt='wealthPlan' className='wealthPlan'/>
      
      <ul className={mobileMenu?'':'hide-mobile-menu'}>
      {/* smooth={true} offset={200} duration={500} */}
        <li><NavLink to='/' className={(e)=>{return e.isActive?"label-select": ""}} >Home</NavLink> </li>
        <li><NavLink to='/Plan'   className={(e)=>{return e.isActive?"label-select": ""}} >Plan</NavLink> </li>
        
      </ul>
      <div className='user-noti'>
        <img src={notifications} alt='logo'/>
        <img src={user} alt='logo'/>
         </div>
      <img src={menu} alt='menu icon' className='menu-icon' onClick={toggleMenu}/>
    </nav>
  );
}

export default Navbar;


// function bg(){
//   return (
//     <div className='bg'>

//     </div>
//   );
// }
// export {bg};

