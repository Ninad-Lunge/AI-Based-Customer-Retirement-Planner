import React from 'react'

import './Body0.css';
import man_img from '../../Assets/Body0/man_img.png';

const Body0 = () => {

  return (
    <div  className='body0 cont order' id='Home'>
        <div className='body0-text'>
            <h1>"Invest Smarter, Retire Stronger: 
            Your Future Starts Here!"</h1>
            <button className='btnn'>Plan Now</button>
            {/* <button type="button" class="btn btn-primary btn-lg">Large button</button> */}      
        </div>
        <div className='body0-img'>
          <img src={man_img} alt='man_img' className='img'/>
         </div>
    </div>
  )
}

export default Body0;


